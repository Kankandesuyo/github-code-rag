import json
import time
from pathlib import Path
from typing import Any, Callable

from app.agents.repository_agent import RepositoryAgent
from app.schemas.report_schema import AgentLog


MANIFEST_VERSION = 4
MANIFEST_DIR_NAME = ".codebase_agent"
MANIFEST_FILE_NAME = "repository_manifest.json"


class RepositoryManifestCache:
    def __init__(self) -> None:
        self.repository_agent = RepositoryAgent()

    def manifest_path(self, repo_path: Path) -> Path:
        return repo_path / MANIFEST_DIR_NAME / MANIFEST_FILE_NAME

    def compute_signature(self, repo_path: Path) -> dict[str, int]:
        files = self.repository_agent.iter_files(repo_path)
        total_size = 0
        max_mtime_ns = 0
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            total_size += stat.st_size
            max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)

        return {
            "manifest_version": MANIFEST_VERSION,
            "file_count": len(files),
            "total_size": total_size,
            "max_mtime_ns": max_mtime_ns,
        }

    def load(self, repo_path: Path, signature: dict[str, int]) -> dict[str, Any] | None:
        path = self.manifest_path(repo_path)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("signature") != signature:
            return None
        return payload

    def save(self, repo_path: Path, signature: dict[str, int], data: dict[str, Any]) -> None:
        path = self.manifest_path(repo_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "signature": signature,
            "generated_at": int(time.time()),
            "data": data,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_or_build(
        self,
        *,
        repo_path: Path,
        builder: Callable[[], dict[str, Any]],
    ) -> tuple[dict[str, Any], list[AgentLog]]:
        started = time.perf_counter()
        signature = self.compute_signature(repo_path)
        cached_payload = self.load(repo_path, signature)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if cached_payload:
            return cached_payload["data"], [
                AgentLog(
                    agent="ManifestCache",
                    action="Loaded repository_manifest.json",
                    duration_ms=duration_ms,
                    cached=True,
                )
            ]

        build_started = time.perf_counter()
        data = builder()
        self.save(repo_path, signature, data)
        build_duration_ms = round((time.perf_counter() - build_started) * 1000, 2)
        return data, [
            AgentLog(
                agent="ManifestCache",
                action="Built and saved repository_manifest.json",
                duration_ms=build_duration_ms,
                cached=False,
            )
        ]
