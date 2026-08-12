from __future__ import annotations

from dataclasses import dataclass
import time

from app.schemas.report_schema import AgentLog
from app.services.file_parser import DocumentChunk
from app.services.llm_service import build_retrieval_queries, rerank_chunks
from app.services.repo_loader import (
    ImportBudget,
    load_repository_ephemeral,
    validate_github_repo_url,
)
from app.services.vector_store import (
    add_chunk,
    expand_neighbor_chunks,
    retrieve_anchor_chunks,
    retrieve_bm25_chunks,
    retrieve_keyword_chunks,
    rrf_fuse,
)
from app.config import get_settings


@dataclass(frozen=True)
class OnlineSearchResult:
    repository_id: str
    files_scanned: int
    chunks_scanned: int
    chunks: list[dict]
    logs: list[AgentLog]


def _chunk_records(chunks: list[DocumentChunk]) -> list[dict]:
    return [
        {
            "content": chunk.content,
            "metadata": dict(chunk.metadata),
            "distance": 0.0,
        }
        for chunk in chunks
    ]


def retrieve_chunks_in_memory(
    all_chunks: list[dict],
    question: str,
    retrieval_queries: list[str],
) -> list[dict]:
    """Rank request-scoped chunks without creating or querying a vector store."""

    if not all_chunks:
        return []

    settings = get_settings()
    queries = [query for query in retrieval_queries if query.strip()] or [question]
    candidate_k = max(settings.retrieval_candidate_k, settings.retrieval_k)

    anchor_chunks = retrieve_anchor_chunks(
        all_chunks,
        question,
        settings.retrieval_k,
    )
    keyword_sets = [
        retrieve_keyword_chunks(all_chunks, query, candidate_k)
        for query in queries
    ]
    bm25_sets = [
        retrieve_bm25_chunks(all_chunks, query, candidate_k)
        for query in queries
    ]
    fused_chunks = rrf_fuse([*keyword_sets, *bm25_sets], candidate_k)

    selected: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for chunk in [*anchor_chunks, *fused_chunks]:
        add_chunk(selected, seen, chunk)
        if len(selected) >= candidate_k:
            break

    expanded = expand_neighbor_chunks(
        selected,
        all_chunks,
        settings.context_expansion_window,
    )
    return rerank_chunks(question, expanded[:candidate_k])


def search_online_repository(
    github_url: str,
    question: str,
    *,
    budget: ImportBudget | None = None,
) -> OnlineSearchResult:
    """Fetch, search, and rank GitHub evidence entirely within one request."""

    active_budget = budget or ImportBudget.from_settings()
    normalized_url = validate_github_repo_url(github_url)

    query_started = time.perf_counter()
    retrieval_queries = build_retrieval_queries(question)
    active_budget.check_deadline()
    query_log = AgentLog(
        agent="OnlineQueryPlanner",
        action="Preparing request-scoped retrieval queries",
        duration_ms=round((time.perf_counter() - query_started) * 1000, 2),
    )

    fetch_started = time.perf_counter()
    repository_id, document_chunks, files_scanned = load_repository_ephemeral(
        normalized_url,
        budget=active_budget,
    )
    active_budget.check_deadline()
    fetch_log = AgentLog(
        agent="OnlineRepositoryReader",
        action="Reading filtered GitHub files without persistent storage",
        duration_ms=round((time.perf_counter() - fetch_started) * 1000, 2),
    )

    retrieval_started = time.perf_counter()
    all_chunks = _chunk_records(document_chunks)
    chunks = retrieve_chunks_in_memory(all_chunks, question, retrieval_queries)
    active_budget.check_deadline()
    retrieval_log = AgentLog(
        agent="InMemoryRetriever",
        action="Ranking temporary evidence without Chroma",
        duration_ms=round((time.perf_counter() - retrieval_started) * 1000, 2),
    )

    return OnlineSearchResult(
        repository_id=repository_id,
        files_scanned=files_scanned,
        chunks_scanned=len(document_chunks),
        chunks=chunks,
        logs=[query_log, fetch_log, retrieval_log],
    )
