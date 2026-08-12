from pathlib import Path
from threading import BoundedSemaphore

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.main import app
from app.services import online_search, repo_loader, vector_store
from app.services.file_parser import ParsedFile
from app.services.online_search import OnlineSearchResult


def _forbidden(name: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"online chat must not call {name}")

    return fail


def _configure_online_test(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path / "repos")
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "admin_username", "")
    monkeypatch.setattr(settings, "admin_password_hash", "")
    monkeypatch.setattr(settings, "auth_session_secret", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    monkeypatch.setattr(settings, "security_audit_enabled", False)
    monkeypatch.setattr(main_module, "close_chroma_client", lambda: None)
    return settings


def test_online_chat_uses_real_in_memory_pipeline_without_persistence(tmp_path, monkeypatch):
    settings = _configure_online_test(monkeypatch, tmp_path)
    remote_files = [
        ParsedFile(
            file_path="app/main.py",
            content=(
                "from fastapi import FastAPI\n"
                "app = FastAPI()\n"
                "@app.get('/health')\n"
                "def health():\n"
                "    return {'status': 'ok'}\n"
            ),
        ),
        ParsedFile(
            file_path="README.md",
            content="# Demo API\nRun with uvicorn app.main:app.\n",
        ),
    ]

    def fake_browse(_url, _repository_id, budget=None, *, persist_manifest=True):
        assert budget is not None
        assert persist_manifest is False
        return remote_files

    monkeypatch.setattr(repo_loader, "browse_github_repository", fake_browse)
    monkeypatch.setattr(
        repo_loader,
        "save_remote_repository_manifest",
        _forbidden("save_remote_repository_manifest"),
    )
    monkeypatch.setattr(
        repo_loader,
        "save_remote_analysis_snapshot",
        _forbidden("save_remote_analysis_snapshot"),
    )
    monkeypatch.setattr(
        main_module,
        "index_chunks_incremental",
        _forbidden("index_chunks_incremental"),
    )
    monkeypatch.setattr(
        vector_store,
        "get_or_create_collection",
        _forbidden("get_or_create_collection"),
    )

    with TestClient(app) as client:
        repositories_before = client.get("/repositories").json()
        response = client.post(
            "/chat/online",
            json={
                "github_url": "https://github.com/example/demo-api",
                "question": "Where is the health endpoint?",
            },
        )
        repositories_after = client.get("/repositories").json()

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "online"
    assert body["repository_saved"] is False
    assert body["files_scanned"] == 2
    assert body["chunks_scanned"] >= 2
    assert any(source["file_path"] == "app/main.py" for source in body["sources"])
    assert any(log["agent"] == "InMemoryRetriever" for log in body["logs"])
    assert response.headers["cache-control"] == "no-store"
    assert repositories_after == repositories_before
    assert not settings.repos_dir.exists()
    assert not settings.chroma_dir.exists()


def test_github_api_browser_can_skip_manifest_persistence(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path / "repos")
    monkeypatch.setattr(repo_loader, "get_github_default_branch", lambda *_args: "main")
    monkeypatch.setattr(
        repo_loader,
        "get_github_tree",
        lambda *_args: [
            {
                "type": "blob",
                "path": "README.md",
                "size": 12,
                "url": "https://api.github.test/blob/1",
            }
        ],
    )
    monkeypatch.setattr(
        repo_loader,
        "fetch_remote_file",
        lambda *_args: ParsedFile(file_path="README.md", content="# Demo"),
    )
    monkeypatch.setattr(
        repo_loader,
        "save_remote_repository_manifest",
        _forbidden("save_remote_repository_manifest"),
    )

    files = repo_loader.browse_github_repository(
        "https://github.com/example/demo.git",
        "example-demo",
        persist_manifest=False,
    )

    assert [item.file_path for item in files] == ["README.md"]
    assert not settings.repos_dir.exists()


def test_api_rate_limit_fallback_preserves_no_persistence_flag(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        repo_loader,
        "get_github_default_branch",
        lambda *_args: (_ for _ in ()).throw(
            repo_loader.RepositoryLoadError(
                "GitHub API rate limit or permission blocked the request; "
                "set server-side GITHUB_TOKEN to raise the limit"
            )
        ),
    )

    def fake_web(_owner, _repo, _repository_id, budget=None, *, persist_manifest=True):
        captured["budget"] = budget
        captured["persist_manifest"] = persist_manifest
        return [ParsedFile(file_path="README.md", content="# Demo")]

    monkeypatch.setattr(repo_loader, "browse_github_repository_via_web", fake_web)

    files = repo_loader.browse_github_repository(
        "https://github.com/example/demo.git",
        "example-demo",
        persist_manifest=False,
    )

    assert files
    assert captured["budget"] is not None
    assert captured["persist_manifest"] is False


def test_online_chat_with_no_evidence_does_not_call_answer_model(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    monkeypatch.setattr(main_module, "close_chroma_client", lambda: None)
    monkeypatch.setattr(
        main_module,
        "search_online_repository",
        lambda *_args, **_kwargs: OnlineSearchResult(
            repository_id="example-demo",
            files_scanned=1,
            chunks_scanned=1,
            chunks=[],
            logs=[],
        ),
    )
    monkeypatch.setattr(main_module, "answer_question", _forbidden("answer_question"))

    with TestClient(app) as client:
        response = client.post(
            "/chat/online",
            json={
                "github_url": "https://github.com/example/demo",
                "question": "unknown symbol",
            },
        )

    assert response.status_code == 200
    assert response.json()["sources"] == []
    assert response.json()["repository_saved"] is False


def test_online_chat_rejects_non_github_url_before_remote_read(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    monkeypatch.setattr(main_module, "close_chroma_client", lambda: None)
    monkeypatch.setattr(
        online_search,
        "load_repository_ephemeral",
        _forbidden("load_repository_ephemeral"),
    )

    with TestClient(app) as client:
        response = client.post(
            "/chat/online",
            json={
                "github_url": "https://example.com/owner/repo",
                "question": "what is this?",
            },
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "only github.com repository URLs are allowed"}


def test_online_chat_releases_capacity_and_redacts_unknown_errors(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    monkeypatch.setattr(main_module, "close_chroma_client", lambda: None)
    monkeypatch.setattr(main_module, "_online_chat_slots", BoundedSemaphore(1))

    responses = iter(
        [
            RuntimeError("ghp_secret-token must never reach the client"),
            OnlineSearchResult(
                repository_id="example-demo",
                files_scanned=1,
                chunks_scanned=1,
                chunks=[],
                logs=[],
            ),
        ]
    )

    def fake_search(*_args, **_kwargs):
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(main_module, "search_online_repository", fake_search)

    request_body = {
        "github_url": "https://github.com/example/demo",
        "question": "what is this?",
    }
    with TestClient(app) as client:
        failed = client.post("/chat/online", json=request_body)
        succeeded = client.post("/chat/online", json=request_body)

    assert failed.status_code == 500
    assert failed.json() == {"detail": "online repository question failed"}
    assert "secret-token" not in failed.text
    assert succeeded.status_code == 200
    assert succeeded.json()["repository_saved"] is False


def test_frontend_defaults_to_online_mode_without_persisting_online_url():
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    script = Path("app/static/app.js").read_text(encoding="utf-8")

    assert 'id="onlineModeButton"' in html
    assert 'id="deepModeButton"' in html
    assert "不保存源码与索引" in html
    assert 'workspaceMode: "online"' in script
    assert 'endpoint = "/chat/online"' in script
    assert "repository_saved" not in script
    assert 'localStorage.setItem("onlineGithubUrl"' not in script
    assert 'setWorkspaceMode("online")' in script
