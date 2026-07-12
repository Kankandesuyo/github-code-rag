from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    app_api_key: str = ""

    repos_dir: Path = Path("repos")
    chroma_dir: Path = Path("chroma_db")
    embedding_provider: str = "sentence_transformers"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    hash_embedding_dimensions: int = 768

    max_file_size_bytes: int = 1_000_000
    max_repository_files: int = 5000
    max_repository_bytes: int = 80_000_000
    github_api_timeout_seconds: int = 30
    github_token: str = ""
    chunk_size: int = 1200
    chunk_overlap: int = 200
    retrieval_k: int = 10
    retrieval_candidate_k: int = 30
    context_expansion_window: int = 1
    enable_query_expansion: bool = True
    enable_hyde: bool = True
    enable_local_rerank: bool = True
    enable_llm_rerank: bool = False
    max_final_context_chunks: int = 10
    enable_debug_routes: bool = False
    rate_limit_window_seconds: int = 60
    rate_limit_max_requests: int = 120
    chroma_anonymized_telemetry: bool = False
    force_https: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return settings
