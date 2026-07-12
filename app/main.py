from pathlib import Path
from collections import deque
from contextlib import asynccontextmanager
import hashlib
import re
import secrets
from threading import Lock
import time
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agents.rag_agent import RAGAgent
from app.config import get_settings
from app.security.auth import (
    SESSION_COOKIE_NAME,
    AuthConfigurationError,
    LoginRequest,
    authorize_request,
    check_login_rate_limit,
    clear_login_attempts,
    create_session_token,
    decode_session_token,
    ensure_auth_ready,
    is_auth_enabled,
    record_failed_login,
    verify_password,
)
from app.schemas import (
    AgentLog,
    ChatRequest,
    ChatResponse,
    GenerateReadmeRequest,
    GenerateReadmeResponse,
    HealthResponse,
    ProjectReportResponse,
    RepositoryLoadRequest,
    RepositoryLoadResponse,
    Source,
)
from app.services.llm_service import LLMServiceError, answer_question, build_retrieval_queries, rerank_chunks
from app.services.report_service import ReportService
from app.services.repo_loader import RepositoryLoadError, load_repository
from app.services.vector_store import (
    VectorStoreError,
    close_chroma_client,
    detect_query_intents,
    expand_query_keywords,
    index_chunks_incremental,
    retrieve_relevant_chunks,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    close_chroma_client()


app = FastAPI(title="GitHub Code RAG", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
rag_agent = RAGAgent()
report_service = ReportService()
REPOSITORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,140}$")
_rate_limit_lock = Lock()
_rate_limit_buckets: dict[str, deque[float]] = {}


@app.middleware("http")
async def security_headers_and_https_redirect(request: Request, call_next):
    settings = get_settings()
    if settings.force_https and request.url.scheme != "https":
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=307)

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    if request.url.scheme == "https" or settings.force_https:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


def require_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    authorize_request(request, x_api_key, csrf_token)


def enforce_rate_limit(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    settings = get_settings()
    max_requests = settings.rate_limit_max_requests
    window_seconds = settings.rate_limit_window_seconds
    if max_requests <= 0 or window_seconds <= 0:
        return

    if settings.app_api_key.strip() and x_api_key:
        bucket_key = f"api-key:{hashlib.sha256(x_api_key.encode('utf-8')).hexdigest()}"
    else:
        client_host = request.client.host if request.client else "unknown"
        bucket_key = f"ip:{client_host}"

    now = time.monotonic()
    cutoff = now - window_seconds
    with _rate_limit_lock:
        bucket = _rate_limit_buckets.setdefault(bucket_key, deque())
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= max_requests:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        bucket.append(now)


def validate_repository_id(repository_id: str) -> None:
    if not REPOSITORY_ID_PATTERN.fullmatch(repository_id):
        raise HTTPException(status_code=400, detail="invalid repository_id")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/auth/status")
def auth_status(request: Request) -> dict:
    if not is_auth_enabled():
        return {"enabled": False, "authenticated": True, "username": None, "csrf_token": None}
    try:
        ensure_auth_ready()
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    session = decode_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))
    return {
        "enabled": True,
        "authenticated": session is not None,
        "username": session.get("sub") if session else None,
        "csrf_token": session.get("csrf") if session else None,
    }


@app.post("/auth/login")
def auth_login(request: Request, credentials: LoginRequest) -> JSONResponse:
    if not is_auth_enabled():
        raise HTTPException(status_code=404, detail="administrator authentication is not enabled")
    try:
        ensure_auth_ready()
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    check_login_rate_limit(request)
    settings = get_settings()
    username_valid = secrets.compare_digest(credentials.username, settings.admin_username)
    password_valid = verify_password(credentials.password, settings.admin_password_hash)
    if not username_valid or not password_valid:
        record_failed_login(request)
        raise HTTPException(status_code=401, detail="invalid username or password")

    clear_login_attempts(request)
    token, csrf_token = create_session_token(settings.admin_username)
    response = JSONResponse(
        {"enabled": True, "authenticated": True, "username": settings.admin_username, "csrf_token": csrf_token}
    )
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.auth_session_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/auth/logout")
def auth_logout(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> Response:
    authorize_request(request, x_api_key, csrf_token)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE_NAME, path="/", samesite="strict")
    return response


@app.get("/repositories", dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def list_repositories() -> dict:
    settings = get_settings()
    repository_ids = [
        path.name
        for path in settings.repos_dir.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    repository_ids.sort()
    return {"repositories": repository_ids}


@app.post(
    "/repository/load",
    response_model=RepositoryLoadResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def repository_load(request: RepositoryLoadRequest) -> RepositoryLoadResponse:
    try:
        repository_id, chunks, files_indexed = load_repository(str(request.github_url))
        index_result = index_chunks_incremental(repository_id, chunks)
    except RepositoryLoadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"unexpected error: {exc}") from exc

    return RepositoryLoadResponse(
        repository_id=repository_id,
        message="repository loaded successfully",
        files_indexed=files_indexed,
        chunks_indexed=index_result.chunks_indexed,
        chunks_written=index_result.chunks_written,
        index_cached=index_result.index_cached,
        changed_files_count=index_result.changed_files_count,
        removed_files_count=index_result.removed_files_count,
    )


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def chat(request: ChatRequest) -> ChatResponse:
    try:
        chunks, logs = rag_agent.search(request.repository_id, request.question)
        if not chunks:
            return ChatResponse(answer="无法从代码库中找到可靠依据。", sources=[], logs=logs)

        writer_started = time.perf_counter()
        answer = answer_question(request.question, chunks)
        logs.append(
            AgentLog(
                agent="AnswerGenerator",
                action="Generating grounded response",
                duration_ms=round((time.perf_counter() - writer_started) * 1000, 2),
            )
        )
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"unexpected error: {exc}") from exc

    seen: set[tuple[str, int]] = set()
    sources: list[Source] = []
    for chunk in chunks:
        metadata = chunk["metadata"]
        key = (metadata["file_path"], int(metadata["chunk_index"]))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            Source(
                file_path=key[0],
                chunk_id=key[1],
                chunk_index=key[1],
                start_line=metadata.get("start_line"),
                end_line=metadata.get("end_line"),
                language=metadata.get("language"),
                symbol_name=metadata.get("symbol_name"),
                symbol_type=metadata.get("symbol_type"),
            )
        )

    return ChatResponse(answer=answer, sources=sources, logs=logs)


@app.get(
    "/repository/report/{repository_id}",
    response_model=ProjectReportResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def repository_report(repository_id: str) -> ProjectReportResponse:
    validate_repository_id(repository_id)
    try:
        return report_service.build_project_report(repository_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"unexpected error: {exc}") from exc


@app.post(
    "/repository/generate-readme",
    response_model=GenerateReadmeResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def repository_generate_readme(request: GenerateReadmeRequest) -> GenerateReadmeResponse:
    validate_repository_id(request.repository_id)
    try:
        return report_service.generate_readme(request.repository_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"unexpected error: {exc}") from exc


@app.post("/debug/retrieval", dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def debug_retrieval(request: ChatRequest) -> dict:
    if not get_settings().enable_debug_routes:
        raise HTTPException(status_code=404, detail="not found")
    validate_repository_id(request.repository_id)
    try:
        retrieval_queries = build_retrieval_queries(request.question)
        candidate_chunks = retrieve_relevant_chunks(request.repository_id, retrieval_queries)
        chunks = rerank_chunks(request.question, candidate_chunks)
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "repository_id": request.repository_id,
        "question": request.question,
        "retrieval_queries": retrieval_queries,
        "keywords": sorted(expand_query_keywords(request.question)),
        "intents": sorted(detect_query_intents(request.question)),
        "candidate_chunks_count": len(candidate_chunks),
        "final_context_chunks_count": len(chunks),
        "chunks": [
            {
                "file_path": chunk["metadata"]["file_path"],
                "chunk_index": int(chunk["metadata"]["chunk_index"]),
                "start_line": chunk["metadata"].get("start_line"),
                "end_line": chunk["metadata"].get("end_line"),
                "language": chunk["metadata"].get("language"),
                "symbol_name": chunk["metadata"].get("symbol_name"),
                "symbol_type": chunk["metadata"].get("symbol_type"),
                "score": chunk.get("distance"),
                "retrieval_rank": chunk.get("retrieval_rank"),
                "rerank_score": chunk.get("rerank_score"),
                "llm_rerank_score": chunk.get("llm_rerank_score"),
                "preview": chunk["content"][:260],
            }
            for chunk in chunks
        ],
    }
