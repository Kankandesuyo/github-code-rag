from pathlib import Path
from collections import deque
from contextlib import asynccontextmanager
import hashlib
import logging
import re
import secrets
from threading import BoundedSemaphore, Lock
import time
from typing import Annotated
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

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
    ensure_deployment_security,
    is_auth_enabled,
    record_failed_login,
    verify_password,
)
from app.security.audit import write_security_audit
from app.schemas import (
    AgentLog,
    ChatRequest,
    ChatResponse,
    GenerateReadmeRequest,
    GenerateReadmeResponse,
    HealthResponse,
    ProjectReportResponse,
    RepositoryDeleteResponse,
    RepositoryListResponse,
    RepositoryLoadRequest,
    RepositoryLoadResponse,
    RepositorySummaryResponse,
    Source,
)
from app.services.llm_service import (
    LLMServiceError,
    answer_question,
    build_retrieval_queries,
    redact_sensitive_text,
    rerank_chunks,
)
from app.services.report_service import ReportService
from app.services.repo_loader import (
    ImportBudget,
    RepositoryLoadError,
    activate_import_budget,
    load_repository,
)
from app.services.repository_catalog import (
    InvalidRepositoryIdError,
    RepositoryCatalogService,
    RepositoryNotFoundError,
)
from app.services.vector_store import (
    VectorStoreError,
    close_chroma_client,
    detect_query_intents,
    expand_query_keywords,
    index_chunks_incremental,
    retrieve_relevant_chunks,
)


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_deployment_security()
    try:
        yield
    finally:
        close_chroma_client()


app = FastAPI(title="GitHub Code RAG", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
rag_agent = RAGAgent()
report_service = ReportService()
repository_catalog_service = RepositoryCatalogService()
REPOSITORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,140}$")
_rate_limit_lock = Lock()
_rate_limit_buckets: dict[str, deque[float]] = {}
_repository_import_slots = BoundedSemaphore(get_settings().max_concurrent_imports)
SAFE_REPOSITORY_IMPORT_DETAILS = frozenset(
    {
        "repository import deadline exceeded",
        "repository import request limit exceeded",
        "repository import directory limit exceeded",
        "only https:// GitHub repository URLs are allowed",
        "only github.com repository URLs are allowed",
        "credentials in GitHub URLs are not allowed",
        "GitHub repository URL must not include params, query, or fragment",
        "GitHub repository URL must be https://github.com/{owner}/{repo}",
        "GitHub repository URL must include owner and repository name",
        "GitHub owner or repository name contains invalid characters",
        "GitHub owner or repository name is invalid",
        "GitHub repository or file was not found",
        "GitHub API rate limit or permission blocked the request; set server-side GITHUB_TOKEN to raise the limit",
        "GitHub API returned invalid JSON",
        "remote GitHub file is too large",
        "remote GitHub file exceeded size limit",
        "GitHub web page or raw file was not found",
        "GitHub web page access was blocked; set server-side GITHUB_TOKEN or try again later",
        "GitHub API did not return a default branch",
        "GitHub repository tree is too large for browser traversal",
        "GitHub API did not return a repository tree",
        "could not detect the GitHub default branch from the browser page",
        "no supported source files were found by GitHub browser traversal",
        "no supported source files were found by browser traversal",
    }
)


def build_https_redirect_url(request: Request, public_base_url: str) -> str:
    parsed_base_url = urlsplit(public_base_url.strip())
    base_path = parsed_base_url.path.rstrip("/")
    raw_path = request.scope.get("raw_path")
    if isinstance(raw_path, bytes):
        request_path = raw_path.decode("ascii")
    else:
        request_path = request.url.path
    if not request_path.startswith("/"):
        request_path = f"/{request_path}"
    redirect_path = f"{base_path}{request_path}" or "/"
    return urlunsplit(
        ("https", parsed_base_url.netloc, redirect_path, request.url.query, "")
    )


@app.middleware("http")
async def security_headers_and_https_redirect(request: Request, call_next):
    settings = get_settings()
    if settings.force_https and request.url.scheme != "https":
        response = RedirectResponse(
            build_https_redirect_url(request, settings.public_base_url),
            status_code=307,
        )
    else:
        response = await call_next(request)

    return apply_security_response_headers(request, response, settings)


def apply_security_response_headers(request: Request, response: Response, settings) -> Response:
    if request.url.path.startswith(("/auth/", "/repositories", "/repository/", "/chat", "/debug/")):
        response.headers["Cache-Control"] = "no-store"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.scheme == "https" or settings.force_https:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.add_middleware(TrustedHostMiddleware, allowed_hosts=get_settings().allowed_hosts)


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
        _prune_rate_limit_buckets(cutoff)
        if bucket_key not in _rate_limit_buckets and len(_rate_limit_buckets) >= settings.rate_limit_max_buckets:
            raise HTTPException(status_code=429, detail="rate limit capacity reached")
        bucket = _rate_limit_buckets.setdefault(bucket_key, deque())
        if len(bucket) >= max_requests:
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        bucket.append(now)


def _prune_rate_limit_buckets(cutoff: float) -> None:
    for key, bucket in list(_rate_limit_buckets.items()):
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if not bucket:
            _rate_limit_buckets.pop(key, None)


def validate_repository_id(repository_id: str) -> None:
    if not REPOSITORY_ID_PATTERN.fullmatch(repository_id):
        raise HTTPException(status_code=400, detail="invalid repository_id")


def public_repository_import_detail(exc: RepositoryLoadError) -> str:
    detail = str(exc)
    if detail in SAFE_REPOSITORY_IMPORT_DETAILS:
        return detail
    return "repository import failed"


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
    try:
        check_login_rate_limit(request)
    except HTTPException:
        write_security_audit("login", "failure", request=request)
        raise
    settings = get_settings()
    username_valid = secrets.compare_digest(credentials.username, settings.admin_username)
    password_valid = verify_password(credentials.password, settings.admin_password_hash)
    if not username_valid or not password_valid:
        record_failed_login(request)
        write_security_audit("login", "failure", request=request)
        raise HTTPException(status_code=401, detail="invalid username or password")

    clear_login_attempts(request)
    token, csrf_token = create_session_token(settings.admin_username)
    write_security_audit("login", "success", request=request)
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


@app.get(
    "/repositories",
    response_model=RepositoryListResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def list_repositories() -> dict:
    items = repository_catalog_service.list_repositories()
    return {
        "repositories": [item.repository_id for item in items],
        "items": [item.to_dict() for item in items],
    }


@app.get(
    "/repositories/{repository_id}",
    response_model=RepositorySummaryResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def get_repository_summary(repository_id: str) -> dict:
    try:
        return repository_catalog_service.get_repository(repository_id).to_dict()
    except InvalidRepositoryIdError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/repositories/{repository_id}",
    response_model=RepositoryDeleteResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def delete_repository(repository_id: str, request: Request) -> dict:
    try:
        result = repository_catalog_service.delete_repository(repository_id).to_dict()
        write_security_audit("repository_delete", "success", request=request, repository_id=repository_id)
        return result
    except InvalidRepositoryIdError as exc:
        write_security_audit("repository_delete", "failure", request=request, repository_id=repository_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RepositoryNotFoundError as exc:
        write_security_audit("repository_delete", "failure", request=request, repository_id=repository_id)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VectorStoreError as exc:
        write_security_audit("repository_delete", "failure", request=request, repository_id=repository_id)
        raise HTTPException(status_code=500, detail="vector store operation failed") from exc
    except Exception as exc:
        logger.error(
            "repository deletion failed repository_id=%s error_type=%s",
            repository_id,
            type(exc).__name__,
        )
        write_security_audit("repository_delete", "failure", request=request, repository_id=repository_id)
        raise HTTPException(status_code=500, detail="repository deletion failed") from exc


@app.post(
    "/repository/load",
    response_model=RepositoryLoadResponse,
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
)
def repository_load(request: RepositoryLoadRequest, http_request: Request) -> RepositoryLoadResponse:
    if not _repository_import_slots.acquire(blocking=False):
        write_security_audit("repository_import", "failure", request=http_request)
        raise HTTPException(status_code=429, detail="repository import capacity reached")
    try:
        budget = ImportBudget.from_settings()
        with activate_import_budget(budget):
            try:
                budget.check_deadline()
                repository_id, chunks, files_indexed = load_repository(str(request.github_url))
                budget.check_deadline()
                index_result = index_chunks_incremental(repository_id, chunks)
                budget.check_deadline()
            except RepositoryLoadError as exc:
                write_security_audit("repository_import", "failure", request=http_request)
                raise HTTPException(status_code=400, detail=public_repository_import_detail(exc)) from exc
            except VectorStoreError as exc:
                write_security_audit("repository_import", "failure", request=http_request)
                raise HTTPException(status_code=500, detail="vector store operation failed") from exc
            except Exception as exc:
                logger.error("repository import failed error_type=%s", type(exc).__name__)
                write_security_audit("repository_import", "failure", request=http_request)
                raise HTTPException(status_code=500, detail="repository import failed") from exc

            write_security_audit(
                "repository_import",
                "success",
                request=http_request,
                repository_id=repository_id,
            )
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
    finally:
        _repository_import_slots.release()


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
        raise HTTPException(status_code=500, detail="vector store operation failed") from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=500, detail="answer generation failed") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("code question failed repository_id=%s error_type=%s", request.repository_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="code question failed") from exc

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
        logger.error("project report failed repository_id=%s error_type=%s", repository_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="project report generation failed") from exc


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
        logger.error("README generation failed repository_id=%s error_type=%s", request.repository_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail="README generation failed") from exc


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
        raise HTTPException(status_code=500, detail="vector store operation failed") from exc

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
                "preview": redact_sensitive_text(chunk["content"])[:260],
            }
            for chunk in chunks
        ],
    }
