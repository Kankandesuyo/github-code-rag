from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    repos_dir: Path = Path("repos")
    chroma_dir: Path = Path("chroma_db")
    embedding_model_name: str = "sentence-transformers/all-MiniLM-L6-v2"

    max_file_size_bytes: int = 1_000_000
    chunk_size: int = 1200
    chunk_overlap: int = 200
    retrieval_k: int = 6

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return settings
