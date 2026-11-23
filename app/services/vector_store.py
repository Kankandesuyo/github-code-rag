import hashlib
import json
import logging
import math
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock, RLock
from uuid import UUID

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError
from rank_bm25 import BM25Okapi

from app.config import get_settings
from app.services.embedding_service import get_embedding_function, get_embedding_signature
from app.services.file_parser import DocumentChunk
from app.services.repository_catalog import RepositoryNotFoundError, resolve_repository_path
from app.utils.atomic_files import atomic_write_json

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class LexicalIndex:
    chunks: list[dict]
    bm25: BM25Okapi | None


# Stripes bound the number of locks held by the store regardless of repository count.
_repository_index_locks = tuple(RLock() for _ in range(64))
_lexical_cache_lock = Lock()
_lexical_cache: OrderedDict[tuple[str, UUID, str], LexicalIndex] = OrderedDict()


def _repository_index_lock(repository_id: str):
    return _repository_index_locks[hash(repository_id) % len(_repository_index_locks)]


def _invalidate_lexical_cache(repository_id: str) -> None:
    with _lexical_cache_lock:
        for key in list(_lexical_cache):
            if key[0] == repository_id:
                del _lexical_cache[key]


@lru_cache
def get_chroma_client() -> chromadb.PersistentClient:
    settings = get_settings()
    return chromadb.PersistentClient(
        path=str(settings.chroma_dir),
        settings=build_chroma_settings(),
    )


def close_chroma_client() -> None:
    with _lexical_cache_lock:
        _lexical_cache.clear()
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
    repository_hash = hashlib.sha1(repository_id.encode("utf-8")).hexdigest()[:16]
    return f"repo_{repository_hash}_{get_embedding_signature()}"


def get_repository_collection(repository_id: str) -> Collection:
    client = get_chroma_client()
    try:
        collection = client.get_collection(
            name=get_collection_name(repository_id), embedding_function=get_embedding_function()
        )
    except NotFoundError as exc:
        manifest = load_vector_index_manifest(repository_id) or {}
        signature = get_embedding_signature()
        legacy_name = f"repo_{repository_id.replace('-', '_')}_{signature}"[:63]
        if (
            manifest.get("repository_id") != repository_id
            or manifest.get("collection_name") != legacy_name
            or manifest.get("embedding_signature") != signature
        ):
            raise RepositoryNotFoundError("repository index not found") from exc
        try:
            collection = client.get_collection(name=legacy_name, embedding_function=get_embedding_function())
        except NotFoundError as legacy_exc:
            raise RepositoryNotFoundError("repository index not found") from legacy_exc
    if (collection.metadata or {}).get("repository_id") != repository_id:
        raise VectorStoreError("index repository mismatch")
    return collection


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
    except NotFoundError:
        logger.debug("no existing collection to replace repository_id=%s", repository_id)
    except Exception as exc:
        raise VectorStoreError("failed to replace repository vector collection") from exc
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=get_embedding_function(),
        metadata={"repository_id": repository_id},
    )


def delete_repository_collections(repository_id: str) -> int:
    """Delete only Chroma collections explicitly owned by a repository."""
    with _repository_index_lock(repository_id):
        _invalidate_lexical_cache(repository_id)
        client = get_chroma_client()
        deleted = 0
        try:
            collections = client.list_collections()
        except Exception as exc:
            raise VectorStoreError("failed to enumerate repository vector collections") from exc

        for collection in collections:
            metadata = getattr(collection, "metadata", None) or {}
            if metadata.get("repository_id") != repository_id:
                continue
            name = getattr(collection, "name", "")
            if not name:
                continue
            try:
                client.delete_collection(name=name)
            except Exception as exc:
                raise VectorStoreError("failed to delete repository vector collection") from exc
            deleted += 1
        return deleted


def make_chunk_id(repository_id: str, chunk: DocumentChunk) -> str:
    return f"{repository_id}:{chunk.metadata['file_path']}:{chunk.metadata['chunk_index']}"


def vector_index_manifest_path(repository_id: str) -> Path:
    project_path = resolve_repository_path(get_settings().repos_dir, repository_id)
    return project_path / VECTOR_INDEX_MANIFEST_DIR / VECTOR_INDEX_MANIFEST_FILE


def hash_file_chunks(chunks: list[DocumentChunk]) -> str:
    digest = hashlib.sha256()
    for chunk in sorted(chunks, key=lambda item: int(item.metadata["chunk_index"])):
        digest.update(str(chunk.metadata.get("chunk_index", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("start_line", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("end_line", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("language", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("symbol_name", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("symbol_type", "")).encode("utf-8"))
        digest.update(str(chunk.metadata.get("github_ref", "")).encode("utf-8"))
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
    atomic_write_json(path, manifest)


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
    with _repository_index_lock(repository_id):
        _invalidate_lexical_cache(repository_id)
        collection = recreate_collection(repository_id)
        return add_chunks_to_collection(collection, repository_id, chunks)


def index_chunks_incremental(repository_id: str, chunks: list[DocumentChunk]) -> IndexResult:
    with _repository_index_lock(repository_id):
        return _index_chunks_incremental(repository_id, chunks)


def _index_chunks_incremental(repository_id: str, chunks: list[DocumentChunk]) -> IndexResult:
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
        _invalidate_lexical_cache(repository_id)
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
        _invalidate_lexical_cache(repository_id)
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

    _invalidate_lexical_cache(repository_id)
    delete_ids: list[str] = []
    for file_path in [*removed_files, *changed_files]:
        delete_ids.extend(previous_files.get(file_path, {}).get("chunk_ids", []))
    if delete_ids:
        collection.delete(ids=delete_ids)

    grouped_chunks = group_chunks_by_file(chunks)
    chunks_to_add = [chunk for file_path in changed_files for chunk in grouped_chunks.get(file_path, [])]
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


HAN_TEXT_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
TOKEN_PATTERN = re.compile(r"[a-z][a-z0-9_]*|[\u3400-\u4dbf\u4e00-\u9fff]+|\d+")


def tokenize_text(text: str) -> list[str]:
    tokens = []
    for token in TOKEN_PATTERN.findall(text.lower()):
        if HAN_TEXT_PATTERN.fullmatch(token):
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
        else:
            tokens.append(token)
            if "_" in token:
                tokens.extend(part for part in token.split("_") if part)
    return tokens


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
    if any(
        trigger in question_lower for trigger in ("启动", "运行", "部署", "安装", "start", "run", "install", "deploy")
    ):
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
    segments = file_path.replace("\\", "/").lower().split("/")
    filename = segments[-1]
    names = set(segments[:-1])
    names.update(re.split(r"[._-]", filename))
    score = 0.0
    if filename == "readme.md" and len(segments) == 1:
        score += 42 if "overview" in intents else 18
    elif "readme" in names:
        score += 6 if "overview" in intents else 9
    if "overview" in intents and len(segments) >= 3 and filename == "readme.md":
        score -= 8
    if names & {"overview", "introduction", "architecture", "guide", "docs", "spec"}:
        score += 5
    if names & {"quickstart", "install", "installation", "contributing", "development"}:
        score += 5
    if filename in {"pyproject.toml", "setup.py", "requirements.txt", "mkdocs.yml", "package.json"}:
        score += 3
    if names & {"test", "tests", "snapshot", "fixture", "fixtures", "locale", "de", "ru", "uk", "ko"}:
        score -= 6
    if "overview" in intents and names & {"tool", "tools", "lint", "bench", "script", "scripts"}:
        score -= 4
    return score


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
        for document, metadata in zip(documents, metadatas, strict=True)
    ]


def build_lexical_index(chunks: list[dict]) -> LexicalIndex:
    tokenized_documents = [
        tokenize_text(f"{chunk['metadata'].get('file_path', '')} {chunk['content']}") for chunk in chunks
    ]
    if not any(tokenized_documents):
        return LexicalIndex(chunks, None)
    bm25 = BM25Okapi(tokenized_documents)
    # Positive IDF keeps common terms searchable even in small repositories.
    frequencies = Counter(term for document in bm25.doc_freqs for term in document)
    for term, frequency in frequencies.items():
        bm25.idf[term] = math.log1p((bm25.corpus_size - frequency + 0.5) / (frequency + 0.5))
    return LexicalIndex(chunks, bm25)


def _load_lexical_index(collection: Collection, repository_id: str) -> LexicalIndex:
    capacity = get_settings().bm25_cache_capacity
    path = vector_index_manifest_path(repository_id)
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if capacity else None
    except FileNotFoundError:
        digest = None
    if digest is None:
        return build_lexical_index(get_all_collection_chunks(collection, repository_id))

    key = (repository_id, collection.id, digest)
    with _lexical_cache_lock:
        cached = _lexical_cache.get(key)
        if cached is not None:
            _lexical_cache.move_to_end(key)
            return cached

    index = build_lexical_index(get_all_collection_chunks(collection, repository_id))
    with _lexical_cache_lock:
        _lexical_cache[key] = index
        while len(_lexical_cache) > capacity:
            _lexical_cache.popitem(last=False)
    return index


def retrieve_bm25_chunks(index: LexicalIndex, query: str, limit: int) -> list[dict]:
    if index.bm25 is None:
        return []

    query_terms = list(expand_query_keywords(query))
    if not query_terms:
        query_terms = tokenize_text(query)
    if not query_terms:
        return []

    intents = detect_query_intents(query)
    scores = index.bm25.get_scores(query_terms)

    scored: list[tuple[float, dict]] = []
    for score, chunk in zip(scores, index.chunks, strict=True):
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

    anchors.sort(
        key=lambda item: (
            item["metadata"]["file_path"].lower() != "readme.md",
            item["metadata"]["file_path"],
            item["metadata"]["chunk_index"],
        )
    )
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

    by_key = {(chunk["metadata"]["file_path"], int(chunk["metadata"]["chunk_index"])): chunk for chunk in all_chunks}
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
                for document, metadata, distance in zip(documents, metadatas, distances, strict=True)
            ]
        )
    return rrf_fuse(result_sets, limit)


def retrieve_relevant_chunks(repository_id: str, question: str | list[str]) -> list[dict]:
    settings = get_settings()
    queries = normalize_queries(question)
    primary_query = queries[0] if queries else ""

    with _repository_index_lock(repository_id):
        try:
            collection = get_repository_collection(repository_id)
            if collection.count() == 0:
                return []
            candidate_k = max(settings.retrieval_candidate_k, settings.retrieval_k)
            index = _load_lexical_index(collection, repository_id)
            all_chunks = index.chunks
            anchor_chunks = retrieve_anchor_chunks(all_chunks, primary_query, settings.retrieval_k)
            bm25_sets = [retrieve_bm25_chunks(index, query, candidate_k) for query in queries]
            bm25_chunks = rrf_fuse(bm25_sets, candidate_k)
            vector_chunks = retrieve_vector_chunks(collection, queries, candidate_k)
        except RepositoryNotFoundError:
            raise
        except Exception as exc:
            raise VectorStoreError(f"failed to retrieve chunks: {exc}") from exc

    chunks: list[dict] = []
    seen: set[tuple[str, int]] = set()

    for chunk in anchor_chunks:
        add_chunk(chunks, seen, chunk)

    fused_chunks = rrf_fuse([bm25_chunks, vector_chunks], candidate_k)
    for chunk in fused_chunks:
        add_chunk(chunks, seen, chunk)

    candidate_chunks = chunks[:candidate_k]
    expanded_chunks = expand_neighbor_chunks(candidate_chunks, all_chunks, settings.context_expansion_window)
    return expanded_chunks[:candidate_k]
