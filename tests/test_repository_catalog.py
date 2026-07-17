import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import auth
from app.services.repository_catalog import (
    InvalidRepositoryIdError,
    RepositoryCatalogService,
    RepositoryNotFoundError,
)
from app.services.vector_store import delete_repository_collections


def write_project(root: Path, repository_id: str, *, files: int = 2, chunks: int = 3) -> Path:
    project = root / repository_id
    manifest_dir = project / ".codebase_agent"
    manifest_dir.mkdir(parents=True)
    (project / "source_snapshot").mkdir()
    (manifest_dir / "remote_repository_manifest.json").write_text(
        json.dumps(
            {
                "github_url": "https://github.com/openai/codex",
                "default_branch": "main",
                "files_indexed": files,
            }
        ),
        encoding="utf-8",
    )
    (manifest_dir / "vector_index_manifest.json").write_text(
        json.dumps(
            {
                "repository_id": repository_id,
                "chunk_count": chunks,
                "files": {f"file-{index}.py": {} for index in range(files)},
            }
        ),
        encoding="utf-8",
    )
    return project


def test_catalog_returns_structured_summaries_newest_first(tmp_path):
    older = write_project(tmp_path, "openai-codex-old", files=4, chunks=9)
    newer = write_project(tmp_path, "openai-codex-new", files=2, chunks=5)
    older.touch()
    newer.touch()
    older_mtime = older.stat().st_mtime - 60
    newer_mtime = newer.stat().st_mtime
    import os

    for path in [*older.rglob("*"), older]:
        os.utime(path, (older_mtime, older_mtime))
    os.utime(newer, (newer_mtime, newer_mtime))

    items = RepositoryCatalogService(tmp_path).list_repositories()

    assert [item.repository_id for item in items] == ["openai-codex-new", "openai-codex-old"]
    assert items[0].owner_id is None
    assert items[0].status == "ready"
    assert items[0].files_indexed == 2
    assert items[0].chunks_indexed == 5
    assert items[0].github_url == "https://github.com/openai/codex"
    assert items[0].default_branch == "main"
    assert items[0].created_at is not None
    assert items[0].updated_at is not None


def test_catalog_tolerates_corrupt_optional_manifests(tmp_path, caplog):
    project = tmp_path / "broken-project"
    manifest_dir = project / ".codebase_agent"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "vector_index_manifest.json").write_text("not-json", encoding="utf-8")

    items = RepositoryCatalogService(tmp_path).list_repositories()

    assert len(items) == 1
    assert items[0].repository_id == "broken-project"
    assert items[0].status == "incomplete"
    assert items[0].files_indexed == 0
    assert "ignored unreadable repository manifest" in caplog.text


def test_catalog_get_rejects_invalid_and_missing_ids(tmp_path):
    service = RepositoryCatalogService(tmp_path)

    with pytest.raises(InvalidRepositoryIdError):
        service.get_repository("../escape")
    with pytest.raises(RepositoryNotFoundError):
        service.get_repository("missing-project")


def test_catalog_delete_is_root_contained_and_calls_vector_cleanup(tmp_path):
    project = write_project(tmp_path, "safe-project")
    sibling = tmp_path.parent / "must-survive"
    sibling.mkdir(exist_ok=True)
    calls: list[str] = []
    service = RepositoryCatalogService(tmp_path, vector_cleanup=lambda repository_id: calls.append(repository_id) or 2)

    result = service.delete_repository("safe-project")

    assert result.collections_deleted == 2
    assert calls == ["safe-project"]
    assert not project.exists()
    assert sibling.exists()


def test_catalog_delete_rejects_invalid_missing_and_symlink(tmp_path):
    service = RepositoryCatalogService(tmp_path, vector_cleanup=lambda _repository_id: 0)

    with pytest.raises(InvalidRepositoryIdError):
        service.delete_repository("../escape")
    with pytest.raises(RepositoryNotFoundError):
        service.delete_repository("missing-project")

    outside = tmp_path.parent / "outside-project"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "linked-project"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are not available in this Windows environment")
    with pytest.raises(InvalidRepositoryIdError):
        service.delete_repository("linked-project")
    assert outside.exists()


def test_repository_api_preserves_legacy_list_and_supports_detail_and_delete(tmp_path, monkeypatch):
    import app.main as main_module

    write_project(tmp_path, "openai-codex-ready")
    service = RepositoryCatalogService(tmp_path, vector_cleanup=lambda _repository_id: 1)
    monkeypatch.setattr(main_module, "repository_catalog_service", service)
    settings = main_module.get_settings()
    monkeypatch.setattr(settings, "admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", auth.hash_password("correct-password"), raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret-with-enough-entropy", raising=False)
    monkeypatch.setattr(settings, "auth_cookie_secure", False, raising=False)
    monkeypatch.setattr(settings, "app_api_key", "automation-key", raising=False)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0, raising=False)

    with TestClient(app) as client:
        listed = client.get("/repositories", headers={"X-API-Key": "automation-key"})
        detail = client.get("/repositories/openai-codex-ready", headers={"X-API-Key": "automation-key"})
        login = client.post("/auth/login", json={"username": "admin", "password": "correct-password"})
        missing_csrf = client.delete("/repositories/openai-codex-ready")
        deleted = client.delete(
            "/repositories/openai-codex-ready",
            headers={"X-CSRF-Token": login.json()["csrf_token"]},
        )
        missing = client.get("/repositories/openai-codex-ready", headers={"X-API-Key": "automation-key"})

    assert listed.status_code == 200
    assert listed.json()["repositories"] == ["openai-codex-ready"]
    assert listed.json()["items"][0]["repository_id"] == "openai-codex-ready"
    assert detail.status_code == 200
    assert missing_csrf.status_code == 403
    assert deleted.json() == {
        "repository_id": "openai-codex-ready",
        "deleted": True,
        "collections_deleted": 1,
    }
    assert missing.status_code == 404


def test_vector_cleanup_matches_repository_metadata_not_name_prefix(monkeypatch):
    import app.services.vector_store as vector_store

    class FakeCollection:
        def __init__(self, name, repository_id):
            self.name = name
            self.metadata = {"repository_id": repository_id}

    class FakeClient:
        def __init__(self):
            self.deleted = []

        def list_collections(self):
            return [
                FakeCollection("repo_target_similar", "target-similar"),
                FakeCollection("unrelated_name", "target"),
                FakeCollection("repo_target", "target"),
            ]

        def delete_collection(self, *, name):
            self.deleted.append(name)

    client = FakeClient()
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: client)

    deleted = delete_repository_collections("target")

    assert deleted == 2
    assert client.deleted == ["unrelated_name", "repo_target"]


def test_repository_catalog_endpoints_publish_typed_openapi_contract():
    with TestClient(app) as client:
        openapi = client.get("/openapi.json").json()

    list_schema = openapi["paths"]["/repositories"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    detail_schema = openapi["paths"]["/repositories/{repository_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    delete_schema = openapi["paths"]["/repositories/{repository_id}"]["delete"]["responses"]["200"]["content"]["application/json"]["schema"]

    assert list_schema["$ref"].endswith("RepositoryListResponse")
    assert detail_schema["$ref"].endswith("RepositorySummaryResponse")
    assert delete_schema["$ref"].endswith("RepositoryDeleteResponse")
