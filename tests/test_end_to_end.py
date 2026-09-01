from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.embedding_service import get_embedding_function
from app.services.file_parser import ParsedFile
from app.services.repo_loader import save_remote_repository_manifest
from app.services.vector_store import get_chroma_client


def test_remote_import_chat_report_and_readme_journey(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path / "repos")
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    settings.repos_dir.mkdir(parents=True, exist_ok=True)
    settings.chroma_dir.mkdir(parents=True, exist_ok=True)
    get_embedding_function.cache_clear()
    get_chroma_client.cache_clear()

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
        ParsedFile(file_path="requirements.txt", content="fastapi\nuvicorn\n"),
        ParsedFile(file_path="README.md", content="# Demo API\nA FastAPI health service.\n"),
    ]
    def fake_browse(github_url, repository_id):
        save_remote_repository_manifest(
            repository_id,
            github_url,
            "main",
            remote_files,
            sum(len(item.content.encode()) for item in remote_files),
            "test_fixture",
        )
        return remote_files

    monkeypatch.setattr("app.services.repo_loader.browse_github_repository", fake_browse)

    with TestClient(app) as client:
        loaded = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo-api"},
        )
        assert loaded.status_code == 200
        repository_id = loaded.json()["repository_id"]
        assert loaded.json()["files_indexed"] == 3

        chat = client.post(
            "/chat",
            json={"repository_id": repository_id, "question": "Where is the FastAPI health endpoint?"},
        )
        assert chat.status_code == 200
        assert chat.json()["sources"]
        assert any(source["file_path"] == "app/main.py" for source in chat.json()["sources"])
        assert any(
            source["url"].startswith(
                "https://github.com/example/demo-api/blob/HEAD/app/main.py#L"
            )
            for source in chat.json()["sources"]
            if source["url"]
        )

        report = client.get(f"/repository/report/{repository_id}")
        assert report.status_code == 200
        assert "FastAPI" in report.json()["technology_stack"]["backend"]
        assert any(item["path"] == "/health" for item in report.json()["api_analysis"])

        readme = client.post(
            "/repository/generate-readme",
            json={"repository_id": repository_id},
        )
        assert readme.status_code == 200
        assert "FastAPI" in readme.json()["markdown"]
