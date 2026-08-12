from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Callable

from app.config import get_settings


logger = logging.getLogger(__name__)
REPOSITORY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,140}$")
MANIFEST_DIR = ".codebase_agent"


class RepositoryCatalogError(RuntimeError):
    pass


class InvalidRepositoryIdError(RepositoryCatalogError):
    pass


class RepositoryNotFoundError(RepositoryCatalogError):
    pass


@dataclass(frozen=True)
class RepositorySummary:
    repository_id: str
    owner_id: str | None
    status: str
    files_indexed: int
    chunks_indexed: int
    created_at: int | None
    updated_at: int | None
    github_url: str | None
    default_branch: str | None
    source: str | None
    source_name: str | None
    source_type: str | None
    display_name: str | None
    upload_name: str | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RepositoryDeletionResult:
    repository_id: str
    deleted: bool
    collections_deleted: int

    def to_dict(self) -> dict:
        return asdict(self)


class RepositoryCatalogService:
    def __init__(
        self,
        repos_dir: Path | None = None,
        *,
        vector_cleanup: Callable[[str], int] | None = None,
    ) -> None:
        self.repos_dir = Path(repos_dir) if repos_dir is not None else get_settings().repos_dir
        self._vector_cleanup = vector_cleanup

    def _validate_repository_id(self, repository_id: str) -> None:
        if not REPOSITORY_ID_PATTERN.fullmatch(repository_id):
            raise InvalidRepositoryIdError("invalid repository_id")

    def _project_path(self, repository_id: str) -> Path:
        self._validate_repository_id(repository_id)
        root = self.repos_dir.resolve()
        candidate = self.repos_dir / repository_id
        if candidate.is_symlink():
            raise InvalidRepositoryIdError("repository path must not be a symlink")
        resolved = candidate.resolve()
        if resolved.parent != root:
            raise InvalidRepositoryIdError("repository path escapes repository root")
        return candidate

    def _read_json(self, path: Path, repository_id: str) -> dict | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            logger.warning(
                "ignored unreadable repository manifest repository_id=%s file=%s error=%s",
                repository_id,
                path.name,
                type(exc).__name__,
            )
            return None
        return payload if isinstance(payload, dict) else None

    def _summarize(self, project_path: Path) -> RepositorySummary:
        repository_id = project_path.name
        manifest_dir = project_path / MANIFEST_DIR
        remote = self._read_json(manifest_dir / "remote_repository_manifest.json", repository_id) or {}
        source_manifest = self._read_json(manifest_dir / "source_manifest.json", repository_id) or {}
        vector = self._read_json(manifest_dir / "vector_index_manifest.json", repository_id)

        try:
            stat = project_path.stat()
            created_at = int(stat.st_ctime)
            updated_at = int(stat.st_mtime)
        except OSError:
            created_at = None
            updated_at = None

        manifest_times: list[int] = []
        for manifest_path in manifest_dir.glob("*.json") if manifest_dir.is_dir() else []:
            try:
                manifest_times.append(int(manifest_path.stat().st_mtime))
            except OSError:
                continue
        if manifest_times:
            updated_at = max([updated_at or 0, *manifest_times])

        files = vector.get("files", {}) if vector else {}
        files_indexed = len(files) if isinstance(files, dict) else 0
        if not files_indexed:
            files_indexed = int(
                source_manifest.get("files_indexed", remote.get("files_indexed", 0)) or 0
            )
        chunks_indexed = int(vector.get("chunk_count", 0) or 0) if vector else 0
        source = source_manifest.get("source")
        if not isinstance(source, str) or not source:
            source = remote.get("source") if isinstance(remote.get("source"), str) else None
        if source is None and remote:
            source = "github"
        source_name = source_manifest.get("source_name")
        if not isinstance(source_name, str) or not source_name:
            source_name = None
        source_type = source_manifest.get("source_type")
        if not isinstance(source_type, str) or not source_type:
            source_type = "zip_upload" if source == "zip_upload" else "github" if remote else source
        display_name = source_manifest.get("display_name")
        if not isinstance(display_name, str) or not display_name:
            display_name = source_name
        if display_name is None:
            github_url = remote.get("github_url")
            if isinstance(github_url, str) and github_url:
                display_name = github_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        upload_name = source_manifest.get("upload_name")
        if not isinstance(upload_name, str) or not upload_name:
            upload_name = source_name if source_type == "zip_upload" else None

        return RepositorySummary(
            repository_id=repository_id,
            owner_id=None,
            status="ready" if vector is not None else "incomplete",
            files_indexed=files_indexed,
            chunks_indexed=chunks_indexed,
            created_at=created_at,
            updated_at=updated_at,
            github_url=remote.get("github_url") if isinstance(remote.get("github_url"), str) else None,
            default_branch=remote.get("default_branch") if isinstance(remote.get("default_branch"), str) else None,
            source=source,
            source_name=source_name,
            source_type=source_type,
            display_name=display_name,
            upload_name=upload_name,
        )

    def list_repositories(self) -> list[RepositorySummary]:
        self.repos_dir.mkdir(parents=True, exist_ok=True)
        items: list[RepositorySummary] = []
        for path in self.repos_dir.iterdir():
            if path.name.startswith(".") or not path.is_dir() or path.is_symlink():
                continue
            if not REPOSITORY_ID_PATTERN.fullmatch(path.name):
                logger.warning("ignored invalid repository directory name=%s", path.name)
                continue
            items.append(self._summarize(path))
        return sorted(items, key=lambda item: (item.updated_at or 0, item.repository_id), reverse=True)

    def get_repository(self, repository_id: str) -> RepositorySummary:
        project_path = self._project_path(repository_id)
        if not project_path.is_dir():
            raise RepositoryNotFoundError("repository not found")
        return self._summarize(project_path)

    def delete_repository(self, repository_id: str) -> RepositoryDeletionResult:
        project_path = self._project_path(repository_id)
        if not project_path.is_dir():
            raise RepositoryNotFoundError("repository not found")

        if self._vector_cleanup is None:
            from app.services.vector_store import delete_repository_collections

            collections_deleted = delete_repository_collections(repository_id)
        else:
            collections_deleted = self._vector_cleanup(repository_id)
        shutil.rmtree(project_path)
        return RepositoryDeletionResult(
            repository_id=repository_id,
            deleted=True,
            collections_deleted=collections_deleted,
        )
