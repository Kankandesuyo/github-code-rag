from fastapi import FastAPI, HTTPException

from app.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    RepositoryLoadRequest,
    RepositoryLoadResponse,
    Source,
)
from app.services.llm_service import LLMServiceError, answer_question
from app.services.repo_loader import RepositoryLoadError, load_repository
from app.services.vector_store import VectorStoreError, index_chunks, retrieve_relevant_chunks


app = FastAPI(title="GitHub Code RAG", version="0.1.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/repository/load", response_model=RepositoryLoadResponse)
def repository_load(request: RepositoryLoadRequest) -> RepositoryLoadResponse:
    try:
        repository_id, chunks, files_indexed = load_repository(str(request.github_url))
        chunks_indexed = index_chunks(repository_id, chunks)
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
        chunks_indexed=chunks_indexed,
    )


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        chunks = retrieve_relevant_chunks(request.repository_id, request.question)
        answer = answer_question(request.question, chunks)
    except VectorStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except LLMServiceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
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
        sources.append(Source(file_path=key[0], chunk_index=key[1]))

    return ChatResponse(answer=answer, sources=sources)
