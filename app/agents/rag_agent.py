import time

from app.schemas.report_schema import AgentLog
from app.services.llm_service import build_retrieval_queries, rerank_chunks
from app.services.vector_store import retrieve_relevant_chunks


class RAGAgent:
    name = "CodeRetriever"

    def search(self, repository_id: str, question: str) -> tuple[list[dict], list[AgentLog]]:
        started = time.perf_counter()
        retrieval_queries = build_retrieval_queries(question)
        candidate_chunks = retrieve_relevant_chunks(repository_id, retrieval_queries)
        chunks = rerank_chunks(question, candidate_chunks)
        logs = [
            AgentLog(
                agent=self.name,
                action="Retrieving related symbols",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
        return chunks, logs
