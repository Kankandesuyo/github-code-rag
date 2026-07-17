import hashlib
import math
import re
from functools import lru_cache

from chromadb.api.types import Documents, Embeddings

from app.config import get_settings


class EmbeddingConfigurationError(RuntimeError):
    pass


class HashEmbeddingFunction:
    def __init__(self, dimensions: int = 768):
        self.dimensions = dimensions

    def __call__(self, input: Documents) -> Embeddings:
        return [self._embed_text(text) for text in input]

    def embed_query(self, input: Documents) -> Embeddings:
        return self(input)

    @staticmethod
    def name() -> str:
        return "github_code_rag_hash"

    def is_legacy(self) -> bool:
        """Use Chroma's compatibility path for application-owned embeddings."""
        return True

    def _embed_text(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[\u4e00-\u9fff]+|\d+", text.lower())

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return vector
        return [value / norm for value in vector]


class SentenceTransformerEmbeddingFunction:
    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingConfigurationError(
                "sentence-transformers is not installed. Run `pip install -r requirements.txt` "
                "or set EMBEDDING_PROVIDER=hash as a temporary fallback."
            ) from exc

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def __call__(self, input: Documents) -> Embeddings:
        embeddings = self.model.encode(
            list(input),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()

    def embed_query(self, input: Documents) -> Embeddings:
        return self(input)

    @staticmethod
    def name() -> str:
        return "github_code_rag_sentence_transformer"

    def is_legacy(self) -> bool:
        """Use Chroma's compatibility path for application-owned embeddings."""
        return True


@lru_cache
def get_embedding_function():
    settings = get_settings()
    provider = settings.embedding_provider.lower().strip()
    if provider == "hash":
        return HashEmbeddingFunction(dimensions=settings.hash_embedding_dimensions)
    if provider in {"sentence_transformers", "sentence-transformers", "local"}:
        return SentenceTransformerEmbeddingFunction(settings.embedding_model_name)
    raise EmbeddingConfigurationError(
        f"Unsupported EMBEDDING_PROVIDER={settings.embedding_provider!r}. "
        "Use `sentence_transformers` or `hash`."
    )


def get_embedding_signature() -> str:
    settings = get_settings()
    provider = settings.embedding_provider.lower().strip()
    model = settings.embedding_model_name if provider != "hash" else str(settings.hash_embedding_dimensions)
    digest = hashlib.sha1(f"{provider}:{model}".encode("utf-8")).hexdigest()[:8]
    return f"{provider.replace('-', '_')}_{digest}"
