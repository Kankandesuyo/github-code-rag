from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, rag_agent
from app.services.embedding_service import get_embedding_function
from app.services.repository_catalog import RepositoryNotFoundError
from app.services.vector_store import close_chroma_client, get_chroma_client, retrieve_relevant_chunks


@pytest.fixture
def isolated_repository_api(tmp_path, monkeypatch):
    settings = get_settings()
    for name, value in {
        "repos_dir": tmp_path / "repos",
        "chroma_dir": tmp_path / "chroma",
        "embedding_provider": "hash",
        "app_api_key": "",
        "deployment_mode": "local",
        "rate_limit_max_requests": 0,
        "enable_debug_routes": True,
        "deepseek_api_key": "",
    }.items():
        monkeypatch.setattr(settings, name, value)
    settings.repos_dir.mkdir()
    close_chroma_client()
    get_embedding_function.cache_clear()
    with TestClient(app) as client:
        yield client, settings.repos_dir
    get_embedding_function.cache_clear()


@pytest.mark.parametrize("repository_id", [".", "..", ".hidden", "outside-link"])
def test_repository_routes_reject_unsafe_paths(isolated_repository_api, repository_id, monkeypatch):
    client, root = isolated_repository_api
    outside = root.parent / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    (root / ".hidden").mkdir()
    if repository_id == "outside-link":
        link = root / repository_id
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            # Windows can prohibit link creation for unprivileged test processes.
            link.mkdir()
            original_is_symlink = Path.is_symlink
            monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))

    for route in ("/chat", "/repository/generate-readme", "/debug/retrieval"):
        response = client.post(route, json={"repository_id": repository_id, "question": "describe"})
        assert response.status_code == (400 if repository_id == "outside-link" else 422), route

    encoded_id = repository_id.replace(".", "%2E")
    assert client.get(f"/repository/report/{encoded_id}").status_code == 400
    assert client.delete(f"/repositories/{encoded_id}").status_code == 400
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not list(root.parent.rglob("repository_manifest.json"))


def test_missing_repository_and_index_do_not_create_collection(isolated_repository_api, monkeypatch):
    client, root = isolated_repository_api
    chroma = get_chroma_client()
    before = chroma.count_collections()
    payload = {"repository_id": "missing", "question": "describe"}

    with monkeypatch.context() as patch:
        def unexpected_search(*_args):
            pytest.fail("missing repositories must be rejected before retrieval")
        patch.setattr(rag_agent, "search", unexpected_search)
        assert client.post("/chat", json=payload).status_code == 404

    (root / "missing").mkdir()
    assert client.post("/chat", json=payload).status_code == 404
    assert client.post("/debug/retrieval", json=payload).status_code == 404
    with pytest.raises(RepositoryNotFoundError):
        retrieve_relevant_chunks("missing", "describe")
    assert chroma.count_collections() == before
