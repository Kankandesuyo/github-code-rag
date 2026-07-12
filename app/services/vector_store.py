import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.services.embedding_service import get_embedding_function, get_embedding_signature
from app.services.file_parser import DocumentChunk


class VectorStoreError(RuntimeError):
    pass


VECTOR_INDEX_MANIFEST_VERSION = 2
VECTOR_INDEX_MANIFEST_DIR = ".codebase_agent"
VECTOR_INDEX_MANIFEST_FILE = "vector_index_manifest.json"


@dataclass
class IndexResult:
    chunks_indexed: int
    chunks_written: int
    files_indexed: int
    index_cached: bool
    changed_files_count: int
    removed_files_count: int


@lru_cache
def get_chroma_client() -> chromadb.PersistentClient:
    settings = get_settings()
    return chromadb.PersistentClient(
        path=str(settings.chroma_dir),
        settings=build_chroma_settings(),
    )


def close_chroma_client() -> None:
    if get_chroma_client.cache_info().currsize == 0:
        return
    client = get_chroma_client()
    try:
        system = getattr(client, "_system", None)
        if system is not None:
            system.stop()
        clear_system_cache = getattr(client, "clear_system_cache", None)
        if callable(clear_system_cache):
            clear_system_cache()
    finally:
        get_chroma_client.cache_clear()


def build_chroma_settings() -> ChromaSettings:
    settings = get_settings()
    return ChromaSettings(anonymized_telemetry=settings.chroma_anonymized_telemetry)


def get_collection_name(repository_id: str) -> str:
    safe_id = repository_id.replace("-", "_")
    signature = get_embedding_signature()
    return f"repo_{safe_id}_{signature}"[:63]


def get_or_create_collection(repository_id: str) -> Collection:
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=get_collection_name(repository_id),
        embedding_function=get_embedding_function(),
        metadata={"repository_id": repository_id},
    )


def recreate_collection(repository_id: str) -> Collection:
    client = get_chroma_client()
    collection_name = get_collection_name(repository_id)
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=get_embedding_function(),
        metadata={"repository_id": repository_id},
    )


def make_chunk_id(repository_id: str, chunk: DocumentChunk) -> str:
    return f"{repository_id}:{chunk.metadata['file_path']}:{chunk.metadata['chunk_index']}"


def vector_index_manifest_path(repository_id: str) -> Path:
    return get_settings().repos_dir / repository_id / VECTOR_INDEX_MANIFEST_DIR / VECTOR_INDEX_MANIFEST_FILE


def hash_file_chunks(chunks: list[DocumentChunk]) -> str:
    digest = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda item: int(item.metadata["chunk_index"])):
        digest.update(str(chunk.metadata.get("chunk_index", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("start_line", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("end_line", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("language", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("symbol_name", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("symbol_type", "")).encode("utf-8"))
        digest.update(chunk.content.encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def group_chunks_by_file(chunks: list[DocumentChunk]) -> dict[str, list[DocumentChunk]]:
    grouped: dict[str, list[DocumentChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.metadata["file_path"], []).append(chunk)
    return grouped


def build_vector_index_manifest(repository_id: str, chunks: list[DocumentChunk]) -> dict:
    grouped = group_chunks_by_file(chunks)
    files = {}
    for file_path, file_chunks in grouped.items():
        ordered_chunks = sorted(file_chunks, key=lambda item: int(item.metadata["chunk_index"]))
        files[file_path] = {
            "hash": hash_file_chunks(ordered_chunks),
            "chunk_count": len(ordered_chunks),
            "chunk_ids": [make_chunk_id(repository_id, chunk) for chunk in ordered_chunks],
        }
    return {
        "manifest_version": VECTOR_INDEX_MANIFEST_VERSION,
        "embedding_signature": get_embedding_signature(),
        "collection_name": get_collection_name(repository_id),
        "repository_id": repository_id,
        "chunk_count": len(chunks),
        "files": files,
    }


def load_vector_index_manifest(repository_id: str) -> dict | None:
    path = vector_index_manifest_path(repository_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_vector_index_manifest(repository_id: str, manifest: dict) -> None:
    path = vector_index_manifest_path(repository_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def add_chunks_to_collection(collection: Collection, repository_id: str, chunks: list[DocumentChunk]) -> int:
    if not chunks:
        return 0

    ids = [make_chunk_id(repository_id, chunk) for chunk in chunks]
    documents = [chunk.content for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]

    try:
        batch_size = 200
        for start in range(0, len(chunks), batch_size):
            end = start + batch_size
            collection.add(
                ids=ids[start:end],
                documents=documents[start:end],
                metadatas=metadatas[start:end],
            )
    except Exception as exc:
        raise VectorStoreError(f"failed to index chunks: {exc}") from exc

    return len(chunks)


def index_chunks(repository_id: str, chunks: list[DocumentChunk]) -> int:
    collection = recreate_collection(repository_id)
    return add_chunks_to_collection(collection, repository_id, chunks)


def index_chunks_incremental(repository_id: str, chunks: list[DocumentChunk]) -> IndexResult:
    current_manifest = build_vector_index_manifest(repository_id, chunks)
    previous_manifest = load_vector_index_manifest(repository_id)
    files_indexed = len(current_manifest["files"])

    can_incremental = (
        previous_manifest is not None
        and previous_manifest.get("manifest_version") == VECTOR_INDEX_MANIFEST_VERSION
        and previous_manifest.get("embedding_signature") == current_manifest["embedding_signature"]
        and previous_manifest.get("collection_name") == current_manifest["collection_name"]
    )

    if not can_incremental:
        collection = recreate_collection(repository_id)
        chunks_written = add_chunks_to_collection(collection, repository_id, chunks)
        save_vector_index_manifest(repository_id, current_manifest)
        return IndexResult(
            chunks_indexed=len(chunks),
            chunks_written=chunks_written,
            files_indexed=files_indexed,
            index_cached=False,
            changed_files_count=files_indexed,
            removed_files_count=0,
        )

    collection = get_or_create_collection(repository_id)
    if chunks and collection.count() == 0:
        collection = recreate_collection(repository_id)
        chunks_written = add_chunks_to_collection(collection, repository_id, chunks)
        save_vector_index_manifest(repository_id, current_manifest)
        return IndexResult(
            chunks_indexed=len(chunks),
            chunks_written=chunks_written,
            files_indexed=files_indexed,
            index_cached=False,
            changed_files_count=files_indexed,
            removed_files_count=0,
        )

    previous_files = previous_manifest.get("files", {})
    current_files = current_manifest["files"]
    removed_files = sorted(set(previous_files) - set(current_files))
    changed_files = sorted(
        file_path
        for file_path, file_info in current_files.items()
        if previous_files.get(file_path, {}).get("hash") != file_info["hash"]
    )

    if not removed_files and not changed_files:
        save_vector_index_manifest(repository_id, current_manifest)
        return IndexResult(
            chunks_indexed=len(chunks),
            chunks_written=0,
            files_indexed=files_indexed,
            index_cached=True,
            changed_files_count=0,
            removed_files_count=0,
        )

    delete_ids: list[str] = []
    for file_path in [*removed_files, *changed_files]:
        delete_ids.extend(previous_files.get(file_path, {}).get("chunk_ids", []))
    if delete_ids:
        collection.delete(ids=delete_ids)

    grouped_chunks = group_chunks_by_file(chunks)
    chunks_to_add = [
        chunk
        for file_path in changed_files
        for chunk in grouped_chunks.get(file_path, [])
    ]
    chunks_written = add_chunks_to_collection(collection, repository_id, chunks_to_add)
    save_vector_index_manifest(repository_id, current_manifest)
    return IndexResult(
        chunks_indexed=len(chunks),
        chunks_written=chunks_written,
        files_indexed=files_indexed,
        index_cached=False,
        changed_files_count=len(changed_files),
        removed_files_count=len(removed_files),
    )


def tokenize_text(text: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*|[\u4e00-\u9fff]|\d+", text.lower())
    return [token for token in tokens if token.strip()]


def expand_query_keywords(question: str) -> set[str]:
    question_lower = question.lower()
    keywords = set(tokenize_text(question_lower))

    startup_triggers = {"启动", "运行", "部署", "安装", "怎么启动", "怎么运行", "start", "run", "install", "deploy"}
    function_triggers = {
        "功能",
        "能力",
        "作用",
        "用途",
        "做什么",
        "是什么",
        "介绍",
        "概览",
        "模块",
        "架构",
        "feature",
        "features",
        "capability",
        "capabilities",
        "purpose",
        "overview",
        "architecture",
        "module",
    }

    if any(trigger in question_lower for trigger in startup_triggers):
        keywords.update(
            {
                "readme",
                "install",
                "installation",
                "run",
                "running",
                "start",
                "serve",
                "server",
                "dev",
                "development",
                "setup",
                "quickstart",
                "uvicorn",
                "fastapi",
                "pip",
                "python",
            }
        )
    if any(trigger in question_lower for trigger in function_triggers):
        keywords.update(
            {
                "readme",
                "overview",
                "introduction",
                "feature",
                "features",
                "capability",
                "capabilities",
                "purpose",
                "usage",
                "module",
                "modules",
                "architecture",
                "component",
                "components",
                "docs",
                "spec",
                "guide",
                "tool",
                "tools",
                "server",
                "sdk",
                "client",
            }
        )

    return {keyword for keyword in keywords if len(keyword) > 1}


def detect_query_intents(question: str) -> set[str]:
    question_lower = question.lower()
    intents: set[str] = set()
    if any(trigger in question_lower for trigger in ("启动", "运行", "部署", "安装", "start", "run", "install", "deploy")):
        intents.add("startup")
    if any(
        trigger in question_lower
        for trigger in (
            "功能",
            "能力",
            "作用",
            "用途",
            "做什么",
            "是什么",
            "介绍",
            "概览",
            "模块",
            "架构",
            "feature",
            "features",
            "capability",
            "purpose",
            "overview",
            "architecture",
            "module",
            "component",
        )
    ):
        intents.add("overview")
    return intents


def path_priority(file_path: str, intents: set[str] | None = None) -> float:
    intents = intents or set()
    path = file_path.lower()
    score = 0.0
    if path == "readme.md":
        score += 42 if "overview" in intents else 18
    elif path.endswith("/readme.md") or "readme" in path:
        score += 6 if "overview" in intents else 9
    if "overview" in intents and path.count("/") >= 2 and path.endswith("readme.md"):
        score -= 8
    if any(name in path for name in ("overview", "introduction", "architecture", "guide", "docs/", "spec")):
        score += 5
    if any(name in path for name in ("quickstart", "install", "installation", "contributing", "development")):
        score += 5
    if path.endswith(("pyproject.toml", "setup.py", "requirements.txt", "mkdocs.yml", "package.json")):
        score += 3
    if any(noisy in path for noisy in ("test", "tests", "snapshot", "fixture", "fixtures", "locale", "/de/", "/ru/", "/uk/", "/ko/")):
        score -= 6
    if "overview" in intents and any(noisy in path for noisy in ("tool", "tools", "lint", "bench", "script", "scripts")):
        score -= 4
    return score


def keyword_score(question: str, document: str, metadata: dict) -> float:
    keywords = expand_query_keywords(question)
    if not keywords:
        return 0.0

    intents = detect_query_intents(question)
    file_path = metadata.get("file_path", "")
    text = f"{file_path}\n{document}".lower()
    score = 0.0
    for keyword in keywords:
        keyword_lower = keyword.lower()
        count = text.count(keyword_lower)
        if count:
            score += 1.0 + min(count, 10) * 1.4
            if keyword_lower in file_path.lower():
                score += 2.5

    score += path_priority(file_path, intents)
    return score


def bm25_score(question_terms: set[str], document: str, metadata: dict, average_length: float, intents: set[str]) -> float:
    if not question_terms:
        return 0.0
    tokens = tokenize_text(f"{metadata.get('file_path', '')}\n{document}")
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    length = len(tokens)
    k1 = 1.5
    b = 0.75
    score = 0.0

    for term in question_terms:
        term_frequency = counts.get(term.lower(), 0)
        if term_frequency == 0:
            continue
        denominator = term_frequency + k1 * (1 - b + b * length / max(average_length, 1))
        score += (term_frequency * (k1 + 1)) / denominator

    return score + path_priority(metadata.get("file_path", ""), intents)


def get_all_collection_chunks(collection: Collection, repository_id: str) -> list[dict]:
    raw = collection.get(
        where={"repository_id": repository_id},
        include=["documents", "metadatas"],
        limit=collection.count(),
    )
    documents = raw.get("documents", [])
    metadatas = raw.get("metadatas", [])
    return [
        {
            "content": document,
            "metadata": metadata,
            "distance": 0.0,
        }
        for document, metadata in zip(documents, metadatas)
    ]


def retrieve_keyword_chunks(all_chunks: list[dict], question: str, limit: int) -> list[dict]:
    question_terms = expand_query_keywords(question)
    intents = detect_query_intents(question)
    token_lengths = [len(tokenize_text(chunk["content"])) for chunk in all_chunks]
    average_length = sum(token_lengths) / max(len(token_lengths), 1)

    scored_chunks: list[dict] = []
    for chunk in all_chunks:
        score = keyword_score(question, chunk["content"], chunk["metadata"])
        score += bm25_score(question_terms, chunk["content"], chunk["metadata"], average_length, intents) * 2.0
        if score <= 0:
            continue
        scored_chunks.append(
            {
                "content": chunk["content"],
                "metadata": chunk["metadata"],
                "distance": -score,
            }
        )

    scored_chunks.sort(key=lambda chunk: chunk["distance"])
    return scored_chunks[:limit]


def retrieve_bm25_chunks(all_chunks: list[dict], query: str, limit: int) -> list[dict]:
    tokenized_documents = [
        tokenize_text(f"{chunk['metadata'].get('file_path', '')}\n{chunk['content']}")
        for chunk in all_chunks
    ]
    if not tokenized_documents:
        return []

    query_terms = list(expand_query_keywords(query))
    if not query_terms:
        query_terms = tokenize_text(query)
    if not query_terms:
        return []

    intents = detect_query_intents(query)
    bm25 = BM25Okapi(tokenized_documents)
    scores = bm25.get_scores(query_terms)

    scored: list[tuple[float, dict]] = []
    for score, chunk in zip(scores, all_chunks):
        combined_score = float(score) + path_priority(chunk["metadata"].get("file_path", ""), intents)
        if combined_score <= 0:
            continue
        scored.append((combined_score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "content": chunk["content"],
            "metadata": chunk["metadata"],
            "distance": -score,
        }
        for score, chunk in scored[:limit]
    ]


def retrieve_anchor_chunks(all_chunks: list[dict], question: str, limit: int) -> list[dict]:
    intents = detect_query_intents(question)
    if not intents:
        return []

    anchors: list[dict] = []
    for chunk in all_chunks:
        metadata = chunk["metadata"]
        file_path = metadata.get("file_path", "").lower()
        chunk_index = int(metadata.get("chunk_index", 0))

        is_root_readme = file_path == "readme.md" and chunk_index <= 6
        is_install_doc = "startup" in intents and file_path in {"docs/install.md", "install.md"} and chunk_index <= 4
        is_root_manifest = file_path in {"package.json", "pyproject.toml", "setup.py", "go.mod"} and chunk_index <= 2
        is_project_doc = (
            "overview" in intents
            and chunk_index <= 3
            and any(name in file_path for name in ("overview", "introduction", "architecture"))
        )

        if not any((is_root_readme, is_install_doc, is_root_manifest, is_project_doc)):
            continue

        anchors.append(
            {
                "content": chunk["content"],
                "metadata": metadata,
                "distance": -10_000.0 + chunk_index,
            }
        )

    anchors.sort(key=lambda item: (item["metadata"]["file_path"].lower() != "readme.md", item["metadata"]["file_path"], item["metadata"]["chunk_index"]))
    return anchors[:limit]


def chunk_key(chunk: dict) -> tuple[str, int]:
    metadata = chunk["metadata"]
    return metadata["file_path"], int(metadata["chunk_index"])


def rrf_fuse(result_sets: list[list[dict]], limit: int, rrf_k: int = 60) -> list[dict]:
    scores: dict[tuple[str, int], float] = {}
    chunks_by_key: dict[tuple[str, int], dict] = {}

    for result_set in result_sets:
        for rank, chunk in enumerate(result_set, start=1):
            key = chunk_key(chunk)
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            chunks_by_key.setdefault(key, chunk)

    ordered_keys = sorted(scores, key=lambda key: scores[key], reverse=True)
    fused: list[dict] = []
    for key in ordered_keys[:limit]:
        chunk = chunks_by_key[key]
        fused.append(
            {
                "content": chunk["content"],
                "metadata": chunk["metadata"],
                "distance": -scores[key],
            }
        )
    return fused


def add_chunk(chunks: list[dict], seen: set[tuple[str, int]], chunk: dict) -> None:
    key = chunk_key(chunk)
    if key in seen:
        return
    seen.add(key)
    chunks.append(chunk)


def expand_neighbor_chunks(selected_chunks: list[dict], all_chunks: list[dict], window: int) -> list[dict]:
    if window <= 0:
        return selected_chunks

    by_key = {
        (chunk["metadata"]["file_path"], int(chunk["metadata"]["chunk_index"])): chunk
        for chunk in all_chunks
    }
    expanded: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for chunk in selected_chunks:
        metadata = chunk["metadata"]
        file_path = metadata["file_path"]
        center = int(metadata["chunk_index"])
        for index in range(center - window, center + window + 1):
            neighbor = by_key.get((file_path, index))
            if not neighbor:
                continue
            neighbor_copy = {
                "content": neighbor["content"],
                "metadata": neighbor["metadata"],
                "distance": chunk.get("distance", 0.0),
            }
            add_chunk(expanded, seen, neighbor_copy)

    return expanded


def normalize_queries(question: str | list[str]) -> list[str]:
    if isinstance(question, str):
        return [question]
    return [item for item in question if item.strip()]


def retrieve_vector_chunks(collection: Collection, queries: list[str], limit: int) -> list[dict]:
    result_sets: list[list[dict]] = []
    for query in queries:
        result = collection.query(query_texts=[query], n_results=limit)
        documents = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        result_sets.append(
            [
                {
                    "content": document,
                    "metadata": metadata,
                    "distance": distance,
                }
                for document, metadata, distance in zip(documents, metadatas, distances)
            ]
        )
    return rrf_fuse(result_sets, limit)


def retrieve_relevant_chunks(repository_id: str, question: str | list[str]) -> list[dict]:
    settings = get_settings()
    collection = get_or_create_collection(repository_id)
    queries = normalize_queries(question)
    primary_query = queries[0] if queries else ""

    try:
        if collection.count() == 0:
            return []
        candidate_k = max(settings.retrieval_candidate_k, settings.retrieval_k)
        all_chunks = get_all_collection_chunks(collection, repository_id)
        anchor_chunks = retrieve_anchor_chunks(all_chunks, primary_query, settings.retrieval_k)
        keyword_chunks = retrieve_keyword_chunks(all_chunks, primary_query, candidate_k)
        bm25_sets = [retrieve_bm25_chunks(all_chunks, query, candidate_k) for query in queries]
        bm25_chunks = rrf_fuse(bm25_sets, candidate_k)
        vector_chunks = retrieve_vector_chunks(collection, queries, candidate_k)
    except Exception as exc:
        raise VectorStoreError(f"failed to retrieve chunks: {exc}") from exc

    chunks: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for chunk in anchor_chunks:
        add_chunk(chunks, seen, chunk)

    fused_chunks = rrf_fuse([keyword_chunks, bm25_chunks, vector_chunks], candidate_k)
    for chunk in fused_chunks:
        add_chunk(chunks, seen, chunk)

    candidate_chunks = chunks[:candidate_k]
    expanded_chunks = expand_neighbor_chunks(candidate_chunks, all_chunks, settings.context_expansion_window)
    return expanded_chunks[:candidate_k]
