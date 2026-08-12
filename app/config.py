from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


DEFAULT_ALLOWED_HOSTS = ("localhost", "127.0.0.1", "testserver")


class Settings(BaseSettings):
    deployment_mode: str = "local"
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_HOSTS)
    )
    public_base_url: str = "https://localhost"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    app_api_key: str = ""
    admin_username: str = ""
    admin_password_hash: str = ""
    auth_session_secret: str = ""
    auth_session_ttl_seconds: int = 28_800
    auth_cookie_secure: bool = False
    login_rate_limit_window_seconds: int = 300
    login_rate_limit_max_attempts: int = 5
    login_rate_limit_max_buckets: int = Field(default=10_000, ge=1)

    repos_dir: Path = Path("repos")
    chroma_dir: Path = Path("chroma_db")
    embedding_provider: str = "sentence_transformers"
    embedding_model_name: str = "BAAI/bge-small-en-v1.5"
    hash_embedding_dimensions: int = 768

    max_file_size_bytes: int = 1_000_000
    max_repository_files: int = 5000
    max_repository_bytes: int = 80_000_000
    max_repository_directories: int = Field(default=1000, ge=1)
    max_repository_requests: int = Field(default=2500, ge=1)
    repository_import_timeout_seconds: int = Field(default=300, ge=1)
    max_concurrent_imports: int = Field(default=1, ge=1, le=1)
    max_concurrent_online_chats: int = Field(default=1, ge=1, le=4)
    max_archive_upload_bytes: int = Field(default=25_000_000, ge=1)
    max_archive_central_directory_bytes: int = Field(default=5_000_000, ge=22)
    max_archive_files: int = Field(default=5000, ge=1)
    max_archive_directories: int = Field(default=1000, ge=1)
    max_archive_uncompressed_bytes: int = Field(default=80_000_000, ge=1)
    max_archive_compression_ratio: float = Field(default=100.0, ge=1.0)
    max_archive_path_depth: int = Field(default=24, ge=1)
    max_archive_path_length: int = Field(default=512, ge=16)
    max_archive_name_length: int = Field(default=255, ge=5)
    max_document_container_files: int = Field(default=2000, ge=1)
    max_document_uncompressed_bytes: int = Field(default=20_000_000, ge=1)
    max_document_compression_ratio: float = Field(default=100.0, ge=1.0)
    max_document_pages: int = Field(default=500, ge=1)
    max_document_sheets: int = Field(default=100, ge=1)
    max_document_rows: int = Field(default=100_000, ge=1)
    max_document_cells: int = Field(default=500_000, ge=1)
    max_document_extracted_characters: int = Field(default=5_000_000, ge=1)
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
    rate_limit_max_buckets: int = Field(default=10_000, ge=1)
    security_audit_enabled: bool = True
    security_audit_log_path: Path = Path("logs/security_audit.jsonl")
    chroma_anonymized_telemetry: bool = False
    force_https: bool = False
    tls_terminated_by_proxy: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, value: object) -> list[str]:
        if value is None:
            return list(DEFAULT_ALLOWED_HOSTS)
        if isinstance(value, str):
            candidates = value.split(",")
        elif isinstance(value, (list, tuple, set)):
            candidates = list(value)
        else:
            raise ValueError("ALLOWED_HOSTS must be a comma-separated string or a list")

        parsed: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, str):
                raise ValueError("ALLOWED_HOSTS entries must be strings")
            host = candidate.strip()
            if host and host not in seen:
                seen.add(host)
                parsed.append(host)
        return parsed or list(DEFAULT_ALLOWED_HOSTS)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    return settings
