from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.agents.techstack_agent import TechStackAgent
from app.config import get_settings
from app.main import _rate_limit_buckets, app
from app.services.file_parser import DocumentChunk, ParsedFile, read_repository_files
from app.services import repo_loader
from app.services import vector_store
from app.services.llm_service import build_context, build_no_key_fallback_answer, redact_sensitive_text
from app.services.repo_loader import RepositoryLoadError, browse_github_repository, load_repository, validate_github_repo_url
from app.services.report_service import ReportService
from app.services.vector_store import build_chroma_settings, build_vector_index_manifest


def make_demo_repo(root: Path) -> str:
    repository_id = "demo-v2-repo"
    repo = root / repository_id
    (repo / "app").mkdir(parents=True)
    (repo / "api").mkdir()
    (repo / "frontend").mkdir()
    (repo / "prisma").mkdir()
    (repo / "app" / "main.py").write_text(
        "\n".join(
            [
                "from app.utils import normalize_name",
                "from fastapi import FastAPI",
                "from sqlalchemy import Column, Integer, String",
                "app = FastAPI()",
                "@app.get('/health')",
                "def health():",
                "    return {'status': 'ok'}",
                "class User(Base):",
                "    id = Column(Integer, primary_key=True)",
                "    name = Column(String)",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "app" / "utils.py").write_text(
        "def normalize_name(value):\n    return value.strip()\n",
        encoding="utf-8",
    )
    (repo / "api" / "router.py").write_text(
        "\n".join(
            [
                "from fastapi import APIRouter",
                "router = APIRouter(prefix='/users')",
                "@router.post('/create')",
                "def create_user():",
                "    return {}",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "app" / "flask_app.py").write_text(
        "\n".join(
            [
                "from flask import Blueprint",
                "bp = Blueprint('api', __name__, url_prefix='/api')",
                "@bp.route('/items', methods=['GET', 'POST'])",
                "def items():",
                "    return 'ok'",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "frontend" / "server.js").write_text(
        "\n".join(
            [
                "const routes = require('./routes');",
                "const express = require('express');",
                "const app = express();",
                "const router = express.Router();",
                "app.get('/status', handler);",
                "router.put('/profile', handler);",
                "app.use('/api', router);",
                "app.route('/orders').get(handler).post(handler);",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "frontend" / "routes.js").write_text(
        "export const routes = [];\n",
        encoding="utf-8",
    )
    (repo / "app" / "models.py").write_text(
        "\n".join(
            [
                "from django.db import models",
                "class Article(models.Model):",
                "    title = models.CharField(max_length=200)",
                "    author = models.ForeignKey('auth.User', on_delete=models.CASCADE)",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "requirements.txt").write_text(
        "fastapi\nuvicorn\nsqlalchemy\nlangchain\nchromadb\n",
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        '{"scripts":{"dev":"vite --host 0.0.0.0","start":"node server.js"},"dependencies":{"react":"latest","vite":"latest"}}',
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    (repo / "prisma" / "schema.prisma").write_text(
        "datasource db {\n  provider = \"postgresql\"\n}\nmodel User {\n  id Int @id\n  email String @unique\n}\n",
        encoding="utf-8",
    )
    return repository_id


def make_stack_repo(root: Path) -> str:
    repository_id = "demo-techstack-repo"
    repo = root / repository_id
    (repo / ".github" / "workflows").mkdir(parents=True)
    repo.mkdir(exist_ok=True)
    (repo / "requirements.txt").write_text(
        "\n".join(
            [
                "django",
                "flask",
                "celery",
                "redis",
                "psycopg[binary]",
                "langgraph",
                "openai",
                "sentence-transformers",
                "transformers",
            ]
        ),
        encoding="utf-8",
    )
    (repo / "pyproject.toml").write_text(
        "\n".join(
            [
                "[project]",
                'dependencies = ["fastapi", "uvicorn", "sqlalchemy", "pymysql"]',
            ]
        ),
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        """
{
  "dependencies": {
    "express": "^4.18.0",
    "next": "^15.0.0",
    "vue": "^3.0.0",
    "typescript": "^5.0.0",
    "prisma": "^6.0.0"
  },
  "devDependencies": {
    "@vue/compiler-sfc": "^3.0.0"
  }
}
""".strip(),
        encoding="utf-8",
    )
    (repo / "Dockerfile").write_text("FROM node:22\n", encoding="utf-8")
    (repo / "docker-compose.yml").write_text(
        "\n".join(
            [
                "services:",
                "  db:",
                "    image: postgres:16",
                "  mysql:",
                "    image: mysql:8",
                "  cache:",
                "    image: redis:7",
                "  proxy:",
                "    image: nginx:alpine",
            ]
        ),
        encoding="utf-8",
    )
    (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    return repository_id


def test_report_service_manifest_cache(tmp_path, monkeypatch):
    repository_id = make_demo_repo(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)

    service = ReportService()
    first = service.build_project_report(repository_id)
    second = service.build_project_report(repository_id)

    assert first.repository_id == repository_id
    assert first.markdown.startswith("# Project Overview")
    assert "FastAPI" in first.technology_stack.backend
    assert "React" in first.technology_stack.frontend
    assert "Docker" in first.technology_stack.devops
    api_paths = {(item.framework, item.method, item.path) for item in first.api_analysis}
    assert ("FastAPI", "GET", "/health") in api_paths
    assert ("FastAPI", "POST", "/users/create") in api_paths
    assert ("Flask", "GET", "/api/items") in api_paths
    assert ("Flask", "POST", "/api/items") in api_paths
    assert ("Express", "GET", "/status") in api_paths
    assert ("Express", "PUT", "/api/profile") in api_paths
    assert ("Express", "GET", "/orders") in api_paths
    assert ("Express", "POST", "/orders") in api_paths
    assert any(item.technology == "Prisma" for item in first.database_analysis)
    dependency_edges = {(item.source_file, item.resolved_target) for item in first.dependency_analysis}
    assert ("app/main.py", "app/utils.py") in dependency_edges
    assert ("frontend/server.js", "frontend/routes.js") in dependency_edges
    assert any(log.agent == "DependencyAnalyzer" for log in first.logs)
    database_details = {(item.technology, item.detail) for item in first.database_analysis}
    assert ("SQLAlchemy", "model User") in database_details
    assert ("Django ORM", "model Article") in database_details
    assert ("Prisma", "model User") in database_details
    assert ("Prisma", "provider postgresql") in database_details
    assert any(log.agent == "ManifestCache" and log.cached is False for log in first.logs)
    assert any(log.agent == "ManifestCache" and log.cached is True for log in second.logs)
    assert (tmp_path / repository_id / ".codebase_agent" / "repository_manifest.json").exists()


def test_techstack_agent_detects_common_backend_frontend_database_devops_and_ai(tmp_path, monkeypatch):
    repository_id = make_stack_repo(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)

    result, logs = TechStackAgent().analyze(repository_id)

    assert {"FastAPI", "Django", "Flask", "Express", "Node.js", "Celery"}.issubset(set(result.backend))
    assert {"Vue", "Next.js", "TypeScript"}.issubset(set(result.frontend))
    assert {"PostgreSQL", "MySQL", "Redis", "Prisma", "SQLAlchemy"}.issubset(set(result.database))
    assert {"Docker", "GitHub Actions", "Nginx", "Uvicorn"}.issubset(set(result.devops))
    assert {"LangGraph", "OpenAI SDK", "SentenceTransformers", "Transformers"}.issubset(set(result.ai))
    assert logs[0].agent == "TechStackAgent"


def test_vector_index_manifest_tracks_file_level_hashes():
    first_chunks = [
        DocumentChunk(
            content="print('hello')",
            metadata={"repository_id": "repo", "file_path": "app/main.py", "chunk_index": 0},
        ),
        DocumentChunk(
            content="# Demo",
            metadata={"repository_id": "repo", "file_path": "README.md", "chunk_index": 0},
        ),
    ]
    second_chunks = [
        DocumentChunk(
            content="print('changed')",
            metadata={"repository_id": "repo", "file_path": "app/main.py", "chunk_index": 0},
        ),
        DocumentChunk(
            content="# Demo",
            metadata={"repository_id": "repo", "file_path": "README.md", "chunk_index": 0},
        ),
    ]

    first_manifest = build_vector_index_manifest("repo", first_chunks)
    second_manifest = build_vector_index_manifest("repo", second_chunks)

    assert first_manifest["files"]["README.md"]["hash"] == second_manifest["files"]["README.md"]["hash"]
    assert first_manifest["files"]["app/main.py"]["hash"] != second_manifest["files"]["app/main.py"]["hash"]
    assert first_manifest["files"]["app/main.py"]["chunk_ids"] == ["repo:app/main.py:0"]


def test_report_and_readme_endpoints(tmp_path, monkeypatch):
    repository_id = make_demo_repo(tmp_path)
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)

    client = TestClient(app)
    report = client.get(f"/repository/report/{repository_id}")
    readme = client.post("/repository/generate-readme", json={"repository_id": repository_id})

    assert report.status_code == 200
    assert report.json()["markdown"].startswith("# Project Overview")
    assert report.json()["logs"][0]["agent"] == "SupervisorAgent"
    assert readme.status_code == 200
    assert readme.json()["markdown"].startswith(f"# {repository_id}")
    assert readme.json()["logs"]


def test_chat_no_evidence_returns_grounded_message(monkeypatch):
    def fake_search(_repository_id: str, _question: str):
        return [], []

    monkeypatch.setattr("app.main.rag_agent.search", fake_search)
    client = TestClient(app)
    response = client.post("/chat", json={"repository_id": "missing", "question": "anything"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "无法从代码库中找到可靠依据。"
    assert payload["sources"] == []
    assert payload["logs"] == []


def test_chat_without_api_key_returns_grounded_fallback(monkeypatch):
    def fake_search(_repository_id: str, _question: str):
        return [
            {
                "content": "FastAPI app is created in app/main.py and exposes a health endpoint.",
                "metadata": {
                    "file_path": "app/main.py",
                    "chunk_index": 0,
                    "start_line": 1,
                    "end_line": 12,
                    "language": "python",
                    "symbol_name": "health",
                    "symbol_type": "function",
                },
            }
        ], []

    class FakeSettings:
        deepseek_api_key = ""

    monkeypatch.setattr("app.main.rag_agent.search", fake_search)
    monkeypatch.setattr("app.services.llm_service.get_settings", lambda: FakeSettings())

    client = TestClient(app)
    response = client.post("/chat", json={"repository_id": "demo", "question": "怎么健康检查？"})

    assert response.status_code == 200
    payload = response.json()
    assert "未配置 DeepSeek API Key" in payload["answer"]
    assert payload["sources"][0]["file_path"] == "app/main.py"
    assert payload["sources"][0]["chunk_id"] == 0


def test_sensitive_values_are_redacted_from_llm_context_and_fallback():
    chunks = [
        {
            "content": "\n".join(
                [
                    "DEEPSEEK_API_KEY=sk-test-secret",
                    "headers = {'Authorization': 'Bearer abcdefghijklmnop'}",
                    "DATABASE_URL='postgresql://user:pass@example.com/db'",
                ]
            ),
            "metadata": {
                "file_path": "app/config.py",
                "chunk_index": 0,
                "start_line": 1,
                "end_line": 3,
                "language": "python",
                "symbol_name": "",
                "symbol_type": "text",
            },
        }
    ]

    redacted = redact_sensitive_text(chunks[0]["content"])
    context = build_context(chunks)
    fallback = build_no_key_fallback_answer("配置在哪里？", chunks)

    combined = "\n".join([redacted, context, fallback])
    assert "sk-test-secret" not in combined
    assert "abcdefghijklmnop" not in combined
    assert "user:pass@example.com" not in combined
    assert "[REDACTED]" in combined


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/openai/codex", "https://github.com/openai/codex.git"),
        ("https://github.com/openai/codex.git", "https://github.com/openai/codex.git"),
    ],
)
def test_validate_github_repo_url_accepts_canonical_urls(url, expected):
    assert validate_github_repo_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/openai/codex",
        "https://github.com/openai/codex/issues",
        "https://github.com/openai/codex?tab=readme",
        "https://127.0.0.1/openai/codex",
        "https://localhost/openai/codex",
        "ftp://github.com/openai/codex",
        "ssh://github.com/openai/codex",
        "file:///tmp/repo",
        "https://user:pass@github.com/openai/codex",
    ],
)
def test_validate_github_repo_url_rejects_unsafe_urls(url):
    with pytest.raises(RepositoryLoadError):
        validate_github_repo_url(url)


def test_load_repository_uses_github_api_browser_without_clone_or_archive(monkeypatch):
    captured: dict[str, str] = {}

    def fake_browse(github_url: str, repository_id: str):
        captured["github_url"] = github_url
        captured["repository_id"] = repository_id
        return [ParsedFile(file_path="README.md", content="# Demo\n\nA test repository.")]

    monkeypatch.setattr("app.services.repo_loader.browse_github_repository", fake_browse)

    repository_id, chunks, files_indexed = load_repository("https://github.com/openai/codex")

    assert repository_id.startswith("openai-codex-")
    assert files_indexed == 1
    assert chunks
    assert captured == {
        "github_url": "https://github.com/openai/codex.git",
        "repository_id": repository_id,
    }


def test_browse_github_repository_traverses_remote_tree_and_saves_metadata(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)
    monkeypatch.setattr(settings, "github_api_timeout_seconds", 3)
    captured: dict[str, str] = {}

    def fake_default_branch(owner: str, repo: str, timeout_seconds: int):
        captured["owner"] = owner
        captured["repo"] = repo
        captured["timeout"] = str(timeout_seconds)
        return "main"

    def fake_tree(_owner: str, _repo: str, _branch: str, _timeout_seconds: int):
        return [
            {"type": "tree", "path": "app"},
            {"type": "blob", "path": "README.md", "url": "https://api.github.test/blob/readme", "size": 20},
            {"type": "blob", "path": ".env", "url": "https://api.github.test/blob/env", "size": 20},
            {"type": "blob", "path": "app/main.py", "url": "https://api.github.test/blob/main", "size": 20},
        ]

    def fake_file(entry: dict, _timeout_seconds: int):
        if entry["path"] == "README.md":
            return ParsedFile(file_path="README.md", content="# Demo")
        if entry["path"] == "app/main.py":
            return ParsedFile(file_path="app/main.py", content="def main():\n    return 'ok'\n")
        raise AssertionError(f"unexpected file fetch: {entry['path']}")

    monkeypatch.setattr("app.services.repo_loader.get_github_default_branch", fake_default_branch)
    monkeypatch.setattr("app.services.repo_loader.get_github_tree", fake_tree)
    monkeypatch.setattr("app.services.repo_loader.fetch_remote_file", fake_file)

    files = browse_github_repository("https://github.com/openai/codex.git", "openai-codex-test")

    assert [file.file_path for file in files] == ["README.md", "app/main.py"]
    assert captured == {"owner": "openai", "repo": "codex", "timeout": "3"}
    manifest = tmp_path / "openai-codex-test" / ".codebase_agent" / "remote_repository_manifest.json"
    assert manifest.exists()
    assert "github_api_browser" in manifest.read_text(encoding="utf-8")


def test_browse_github_repository_falls_back_to_web_browser_on_api_rate_limit(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)
    monkeypatch.setattr(settings, "github_api_timeout_seconds", 3)
    monkeypatch.setattr(settings, "max_repository_files", 10)
    monkeypatch.setattr(settings, "max_repository_bytes", 100_000)

    def fake_default_branch(_owner: str, _repo: str, _timeout_seconds: int):
        raise RepositoryLoadError("GitHub API rate limit or permission blocked the request; set server-side GITHUB_TOKEN to raise the limit")

    root_page = """
      <a href="/openai/codex/tree/main/app">app</a>
      <a href="/openai/codex/blob/main/README.md">README.md</a>
    """
    app_page = """
      <a href="/openai/codex/blob/main/app/main.py">main.py</a>
      <a href="/openai/codex/blob/main/app/.env">.env</a>
    """

    def fake_fetch_text(url: str, _timeout_seconds: int, accept: str = "text/html"):
        if url == "https://github.com/openai/codex":
            return root_page
        if url == "https://github.com/openai/codex/tree/main/app":
            return app_page
        raise AssertionError(f"unexpected page fetch: {url} {accept}")

    def fake_fetch_raw(owner: str, repo: str, branch: str, file_path: str, _timeout_seconds: int):
        assert (owner, repo, branch) == ("openai", "codex", "main")
        return ParsedFile(file_path=file_path, content=f"content for {file_path}")

    monkeypatch.setattr("app.services.repo_loader.get_github_default_branch", fake_default_branch)
    monkeypatch.setattr("app.services.repo_loader.fetch_url_text", fake_fetch_text)
    monkeypatch.setattr("app.services.repo_loader.fetch_web_raw_file", fake_fetch_raw)

    files = browse_github_repository("https://github.com/openai/codex.git", "openai-codex-test")

    assert [file.file_path for file in files] == ["README.md", "app/main.py"]
    manifest = tmp_path / "openai-codex-test" / ".codebase_agent" / "remote_repository_manifest.json"
    assert "github_web_browser" in manifest.read_text(encoding="utf-8")


def test_read_repository_files_skips_sensitive_files_and_symlinks(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
    (repo / ".env.local").write_text("SECRET_TOKEN=leak\n", encoding="utf-8")
    (repo / "private.pem").write_text("PRIVATE KEY\n", encoding="utf-8")

    outside = tmp_path / "outside.txt"
    outside.write_text("outside secret\n", encoding="utf-8")
    symlink_path = repo / "linked.txt"
    try:
        symlink_path.symlink_to(outside)
    except OSError:
        symlink_path.write_text("fallback\n", encoding="utf-8")

    settings = get_settings()
    monkeypatch.setattr(settings, "max_repository_files", 100)
    monkeypatch.setattr(settings, "max_repository_bytes", 10_000)
    files = read_repository_files(repo)
    paths = {item.file_path for item in files}

    assert "README.md" in paths
    assert ".env.local" not in paths
    assert "private.pem" not in paths
    if symlink_path.is_symlink():
        assert "linked.txt" not in paths


def test_debug_retrieval_disabled_by_default(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_debug_routes", False)
    client = TestClient(app)

    response = client.post("/debug/retrieval", json={"repository_id": "demo", "question": "test"})

    assert response.status_code == 404


def test_api_key_auth_is_disabled_when_not_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    client = TestClient(app)

    response = client.get("/repositories")

    assert response.status_code == 200


def test_api_key_auth_rejects_missing_or_wrong_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "test-secret")
    client = TestClient(app)

    missing = client.get("/repositories")
    wrong = client.get("/repositories", headers={"X-API-Key": "wrong"})

    assert missing.status_code == 401
    assert wrong.status_code == 401


def test_api_key_auth_accepts_correct_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "test-secret")
    client = TestClient(app)

    response = client.get("/repositories", headers={"X-API-Key": "test-secret"})

    assert response.status_code == 200


def test_rate_limit_rejects_excessive_requests(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 2)
    _rate_limit_buckets.clear()
    client = TestClient(app)

    first = client.get("/repositories")
    second = client.get("/repositories")
    third = client.get("/repositories")

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


def test_rate_limit_can_be_disabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    _rate_limit_buckets.clear()
    client = TestClient(app)

    responses = [client.get("/repositories") for _ in range(3)]

    assert [response.status_code for response in responses] == [200, 200, 200]


def test_chroma_telemetry_is_disabled_by_default(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "chroma_anonymized_telemetry", False)

    chroma_settings = build_chroma_settings()

    assert chroma_settings.anonymized_telemetry is False


def test_close_chroma_client_stops_system_and_clears_cache(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")

    class FakeSystem:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    class FakeClient:
        def __init__(self):
            self._system = FakeSystem()

    clients = []

    def fake_persistent_client(**_kwargs):
        client = FakeClient()
        clients.append(client)
        return client

    monkeypatch.setattr(vector_store.chromadb, "PersistentClient", fake_persistent_client)
    vector_store.get_chroma_client.cache_clear()
    first = vector_store.get_chroma_client()

    vector_store.close_chroma_client()
    second = vector_store.get_chroma_client()

    assert first._system.stopped is True
    assert second is not first


def test_application_shutdown_closes_chroma_client(monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.close_chroma_client", lambda: calls.append("closed"))

    with TestClient(app):
        pass

    assert calls == ["closed"]


def test_security_headers_are_set(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "force_https", False)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_force_https_redirects_plain_http(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "force_https", True)
    client = TestClient(app)

    response = client.get("/health", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://")


def test_repository_report_rejects_invalid_repository_id():
    client = TestClient(app)

    response = client.get("/repository/report/bad$repo")

    assert response.status_code == 400


def test_remote_analysis_snapshot_feeds_report_without_unsafe_files(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)
    repository_id = "remote-report-repo"
    files = [
        ParsedFile(
            file_path="app/main.py",
            content="from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef health(): return {'status': 'ok'}\n",
        ),
        ParsedFile(file_path="requirements.txt", content="fastapi\nuvicorn\n"),
        ParsedFile(file_path=".env", content="DEEPSEEK_API_KEY=must-not-persist\n"),
        ParsedFile(file_path="../escape.py", content="print('escape')\n"),
    ]

    snapshot = repo_loader.save_remote_analysis_snapshot(repository_id, files)
    report = ReportService().build_project_report(repository_id)

    assert snapshot == tmp_path / repository_id / "source_snapshot"
    assert (snapshot / "app" / "main.py").exists()
    assert not (snapshot / ".env").exists()
    assert not (tmp_path / repository_id / "escape.py").exists()
    assert "FastAPI" in report.technology_stack.backend
    assert any(item.path == "/health" and item.file_path == "app/main.py" for item in report.api_analysis)
