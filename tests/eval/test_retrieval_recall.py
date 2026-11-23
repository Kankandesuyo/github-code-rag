from pathlib import Path

import pytest

from app.agents.rag_agent import RAGAgent
from app.config import get_settings
from app.services import vector_store as store
from app.services.embedding_service import get_embedding_function
from app.services.file_parser import ParsedFile, read_repository_files, split_files_into_chunks
from app.services.repository_catalog import RepositoryCatalogService, RepositoryNotFoundError

QUESTIONS = [
    ("Where is the health readiness endpoint?", {"app/routes/health.py"}),
    ("Which code validates signed session cookies?", {"app/security/sessions.py"}),
    ("How does the token bucket rate limit a client?", {"app/security/throttle.py"}),
    ("Where is the archive decompression size limit checked?", {"app/importers/zip_reader.py"}),
    ("Where can I delete a saved repository from the catalog?", {"app/storage/catalog.py"}),
    ("How does reciprocal rank fusion merge vector and lexical search?", {"app/search/fusion.py"}),
    ("Where are security audit events written?", {"app/observability/audit.py"}),
    ("Where is the GitHub source permalink pinned to a commit SHA?", {"app/source/links.py"}),
    ("Where is the embedding provider configured?", {"app/config.py"}),
    ("How are query rewrites deduplicated?", {"app/search/query.py"}),
    ("How do I install and start the service with uvicorn?", {"README.md"}),
    ("中文检索怎样切分？多语言嵌入模型如何使用？", {"docs/chinese.md"}),
    (
        "Which modules validate login sessions and enforce rate limits?",
        {"app/security/sessions.py", "app/security/throttle.py"},
    ),
]


@pytest.fixture
def hash_index(tmp_path, monkeypatch):
    settings = get_settings()
    for name, value in {
        "repos_dir": tmp_path / "repos",
        "chroma_dir": tmp_path / "chroma",
        "embedding_provider": "hash",
        "deepseek_api_key": "",
        "enable_local_rerank": True,
        "enable_llm_rerank": False,
        "bm25_cache_capacity": 2,
    }.items():
        monkeypatch.setattr(settings, name, value)
    store.close_chroma_client()
    get_embedding_function.cache_clear()
    yield settings
    store.close_chroma_client()
    get_embedding_function.cache_clear()


def test_retrieval_recall_at_three(hash_index):
    fixture = Path(__file__).with_name("fixture_repo")
    repository_id = "retrieval-fixture"
    files = read_repository_files(fixture)
    chunks = split_files_into_chunks(files, repository_id)
    store.index_chunks_incremental(repository_id, chunks)
    agent = RAGAgent()
    recalls = []
    misses = []
    for question, expected in QUESTIONS:
        ranked, _ = agent.search(repository_id, question)
        top_files = list(dict.fromkeys(chunk["metadata"]["file_path"] for chunk in ranked))[:3]
        recall = len(expected.intersection(top_files)) / len(expected)
        recalls.append(recall)
        if recall < 1:
            misses.append((question, sorted(expected), top_files))
    score = sum(recalls) / len(recalls)
    print(f"Recall@3 = {score:.3f} across {len(QUESTIONS)} questions")
    assert score >= 0.85, f"Recall@3={score:.3f}; missed questions: {misses}"


def test_bm25_cache_hits_lru_eviction_manifest_changes_and_delete(hash_index, monkeypatch):
    calls = []
    original = store.get_all_collection_chunks

    def observed_get(collection, repository_id):
        calls.append(repository_id)
        return original(collection, repository_id)

    monkeypatch.setattr(store, "get_all_collection_chunks", observed_get)

    def index(repository_id, text):
        chunks = split_files_into_chunks([ParsedFile("module.py", f'""" {text} """')], repository_id)
        store.index_chunks_incremental(repository_id, chunks)

    def query(repository_id):
        return store.retrieve_relevant_chunks(repository_id, "account lookup")

    for repository_id in ("one", "two", "three"):
        index(repository_id, "account lookup")
    query("one")
    query("two")
    query("one")
    query("three")
    query("two")
    assert calls == ["one", "two", "three", "two"]

    manifest = store.load_vector_index_manifest("two")
    manifest["revision"] = "changed"
    store.save_vector_index_manifest("two", manifest)
    query("two")
    assert calls.count("two") == 3

    # Invalidate even when a rebuild writes identical manifest bytes.
    assert store._lexical_cache
    index("two", "customer lookup")
    assert "customer lookup" in query("two")[0]["content"]
    assert calls.count("two") == 4

    RepositoryCatalogService().delete_repository("two")
    assert all(key[0] != "two" for key in store._lexical_cache)
    with pytest.raises(RepositoryNotFoundError):
        query("two")


def test_chinese_tokenization_and_path_segment_matches():
    assert store.tokenize_text("中文检索 SQL") == ["中文", "文检", "检索", "sql"]
    assert store.tokenize_text("仓库。搜索") == ["仓库", "搜索"]
    for path in ("src/latest.py", "contest/main.py", "src/latest/schema.py", "src/contest.py"):
        assert store.path_priority(path) == 0
    for path in ("tests/api.py", "src/test_api.py", "src/api.test.js", "fixtures/sample.py"):
        assert store.path_priority(path) < 0
