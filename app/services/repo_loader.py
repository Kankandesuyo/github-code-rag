import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

from git import Repo

from app.config import get_settings
from app.services.file_parser import DocumentChunk, read_repository_files, split_files_into_chunks


class RepositoryLoadError(RuntimeError):
    pass


def generate_repository_id(github_url: str) -> str:
    normalized = github_url.rstrip("/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    parsed = urlparse(normalized)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    readable = "-".join(parts[-2:]) if len(parts) >= 2 else "repository"
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", readable).strip("-")
    return f"{readable}-{digest}"


def clone_repository(github_url: str, repository_id: str) -> Path:
    settings = get_settings()
    target_path = settings.repos_dir / repository_id

    if target_path.exists():
        shutil.rmtree(target_path)

    try:
        Repo.clone_from(github_url, target_path)
    except Exception as exc:
        if target_path.exists():
            shutil.rmtree(target_path, ignore_errors=True)
        raise RepositoryLoadError(f"failed to clone repository: {exc}") from exc

    return target_path


def load_repository(github_url: str) -> tuple[str, list[DocumentChunk], int]:
    repository_id = generate_repository_id(github_url)
    repo_path = clone_repository(github_url, repository_id)
    files = read_repository_files(repo_path)
    chunks = split_files_into_chunks(files, repository_id)
    return repository_id, chunks, len(files)
