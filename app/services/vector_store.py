from functools import lru_cache

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction

from app.config import get_settings
from app.services.file_parser import DocumentChunk


class VectorStoreError(RuntimeError):
    pass


@lru_cache
def get_chroma_client() -> chromadb.PersistentClient:
    settings = get_settings()
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


@lru_cache
def get_embedding_function() -> SentenceTransformerEmbeddingFunction:
    settings = get_settings()
    return SentenceTransformerEmbeddingFunction(model_name=settings.embedding_model_name)


def get_collection_name(repository_id: str) -> str:
    safe_id = repository_id.replace("-", "_")
    return f"repo_{safe_id}"[:63]


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


def index_chunks(repository_id: str, chunks: list[DocumentChunk]) -> int:
    if not chunks:
        return 0

    collection = recreate_collection(repository_id)
    ids = [f"{repository_id}:{chunk.metadata['file_path']}:{chunk.metadata['chunk_index']}" for chunk in chunks]
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


def retrieve_relevant_chunks(repository_id: str, question: str) -> list[dict]:
    settings = get_settings()
    collection = get_or_create_collection(repository_id)

    try:
        if collection.count() == 0:
            return []
        result = collection.query(query_texts=[question], n_results=settings.retrieval_k)
    except Exception as exc:
        raise VectorStoreError(f"failed to retrieve chunks: {exc}") from exc

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    chunks: list[dict] = []
    for document, metadata, distance in zip(documents, metadatas, distances):
        chunks.append(
            {
                "content": document,
                "metadata": metadata,
                "distance": distance,
            }
        )
    return chunks
