from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.main import app
from app.services import repo_loader
from app.services.repo_loader import ImportBudget, RepositoryLoadError
from app.services.vector_store import IndexResult, VectorStoreError


class _FakeResponse:
    def __init__(self, data: bytes, on_read=None):
        self._data = BytesIO(data)
        self._on_read = on_read
        self.headers: dict[str, str] = {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size: int = -1) -> bytes:
        if self._on_read is not None:
            self._on_read()
        return self._data.read(size)


def _index_result() -> IndexResult:
    return IndexResult(
        chunks_indexed=0,
        chunks_written=0,
        files_indexed=1,
        index_cached=False,
        changed_files_count=1,
        removed_files_count=0,
    )


def test_import_security_settings_have_bounded_defaults_and_validate_capacity():
    settings = Settings(_env_file=None)

    assert settings.max_repository_directories == 1000
    assert settings.max_repository_requests == 2500
    assert settings.repository_import_timeout_seconds == 300
    assert settings.max_concurrent_imports == 1

    with pytest.raises(ValidationError):
        Settings(max_concurrent_imports=0, _env_file=None)
    with pytest.raises(ValidationError):
        Settings(max_concurrent_imports=2, _env_file=None)


def test_import_budget_enforces_request_limit():
    budget = ImportBudget(
        max_requests=1,
        max_directories=10,
        timeout_seconds=30,
        clock=lambda: 0.0,
    )

    budget.record_request()

    with pytest.raises(RepositoryLoadError, match="^repository import request limit exceeded$"):
        budget.record_request()
    assert budget.outbound_requests == 1


def test_import_budget_enforces_directory_limit():
    budget = ImportBudget(
        max_requests=10,
        max_directories=1,
        timeout_seconds=30,
        clock=lambda: 0.0,
    )

    budget.record_directory()

    with pytest.raises(RepositoryLoadError, match="^repository import directory limit exceeded$"):
        budget.record_directory()
    assert budget.visited_directories == 1


def test_import_budget_uses_injected_monotonic_clock_for_deadline():
    now = [100.0]
    budget = ImportBudget(
        max_requests=10,
        max_directories=10,
        timeout_seconds=5,
        clock=lambda: now[0],
    )
    now[0] = 105.0

    with pytest.raises(RepositoryLoadError, match="^repository import deadline exceeded$"):
        budget.check_deadline()


def test_import_budget_caps_configured_timeout_to_remaining_seconds():
    now = [100.0]
    budget = ImportBudget(
        max_requests=10,
        max_directories=10,
        timeout_seconds=5,
        clock=lambda: now[0],
    )
    now[0] = 102.0

    assert budget.remaining_seconds() == pytest.approx(3.0)
    assert budget.effective_timeout(30) == pytest.approx(3.0)
    assert budget.effective_timeout(1) == pytest.approx(1.0)


@pytest.mark.parametrize("request_kind", ["api_json", "web_bytes"])
def test_outbound_request_caps_socket_timeout_and_rejects_expiry_after_read(
    monkeypatch, request_kind
):
    now = [100.0]
    budget = ImportBudget(
        max_requests=10,
        max_directories=10,
        timeout_seconds=5,
        clock=lambda: now[0],
    )
    now[0] = 102.0
    observed_timeouts = []

    def fake_urlopen(_request, timeout):
        observed_timeouts.append(timeout)
        payload = b'{"default_branch":"main"}' if request_kind == "api_json" else b"page"
        return _FakeResponse(payload, on_read=lambda: now.__setitem__(0, 105.0))

    monkeypatch.setattr(repo_loader, "urlopen", fake_urlopen)

    with pytest.raises(RepositoryLoadError, match="^repository import deadline exceeded$"):
        if request_kind == "api_json":
            repo_loader.fetch_github_json("https://api.github.test/repo", 30, budget=budget)
        else:
            repo_loader.fetch_url_bytes(
                "https://github.test/page",
                30,
                "text/html",
                budget=budget,
            )

    assert observed_timeouts == [pytest.approx(3.0)]


def test_api_rate_limit_fallback_shares_budget_across_api_html_and_raw_requests(
    tmp_path, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)
    page = b'<a href="/openai/codex/blob/main/README.md">README.md</a>'

    def fake_urlopen(request, timeout):
        url = request.full_url
        assert timeout == settings.github_api_timeout_seconds
        if url == "https://api.github.com/repos/openai/codex":
            raise HTTPError(url, 403, "forbidden", {}, None)
        if url == "https://github.com/openai/codex":
            return _FakeResponse(page)
        if url == "https://raw.githubusercontent.com/openai/codex/main/README.md":
            return _FakeResponse(b"# Demo\n")
        raise AssertionError(f"unexpected request: {url}")

    monkeypatch.setattr(repo_loader, "urlopen", fake_urlopen)
    budget = ImportBudget(
        max_requests=3,
        max_directories=1,
        timeout_seconds=30,
        clock=lambda: 0.0,
    )

    files = repo_loader.browse_github_repository(
        "https://github.com/openai/codex.git",
        "openai-codex-test",
        budget=budget,
    )

    assert [item.file_path for item in files] == ["README.md"]
    assert budget.outbound_requests == 3
    assert budget.visited_directories == 1


def test_load_repository_reuses_outer_budget_through_chunk_split(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path)
    now = [0.0]
    budget = ImportBudget(
        max_requests=10,
        max_directories=10,
        timeout_seconds=5,
        clock=lambda: now[0],
    )

    def unexpected_new_budget(_cls):
        raise AssertionError("load_repository replaced the active import budget")

    def fake_browse(_url, _repository_id):
        assert repo_loader._resolve_import_budget() is budget
        return [repo_loader.ParsedFile(file_path="README.md", content="# Demo")]

    def fake_split(_files, _repository_id):
        assert repo_loader._resolve_import_budget() is budget
        now[0] = 5.0
        return []

    monkeypatch.setattr(ImportBudget, "from_settings", classmethod(unexpected_new_budget))
    monkeypatch.setattr(repo_loader, "browse_github_repository", fake_browse)
    monkeypatch.setattr(repo_loader, "save_remote_analysis_snapshot", lambda *_args: tmp_path)
    monkeypatch.setattr(repo_loader, "split_files_into_chunks", fake_split)

    with repo_loader.activate_import_budget(budget):
        with pytest.raises(RepositoryLoadError, match="^repository import deadline exceeded$"):
            repo_loader.load_repository("https://github.com/example/demo")


def test_repository_load_returns_429_while_import_slot_is_busy(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    started = Event()
    release = Event()

    def blocking_load(_github_url: str):
        started.set()
        assert release.wait(timeout=5)
        return "example-demo-123", [], 1

    monkeypatch.setattr("app.main.load_repository", blocking_load)
    monkeypatch.setattr("app.main.index_chunks_incremental", lambda *_args: _index_result())

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            client.post,
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )
        assert started.wait(timeout=5)
        try:
            second = client.post(
                "/repository/load",
                json={"github_url": "https://github.com/example/other"},
            )
            assert second.status_code == 429
            assert second.json() == {"detail": "repository import capacity reached"}
        finally:
            release.set()

        assert first_future.result(timeout=5).status_code == 200


def test_repository_load_keeps_import_slot_while_vector_index_is_running(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    indexing_started = Event()
    release_index = Event()
    monkeypatch.setattr("app.main.load_repository", lambda _url: ("example-demo-123", [], 1))

    def blocking_index(*_args):
        indexing_started.set()
        assert release_index.wait(timeout=5)
        return _index_result()

    monkeypatch.setattr("app.main.index_chunks_incremental", blocking_index)

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=1) as executor:
        first_future = executor.submit(
            client.post,
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )
        assert indexing_started.wait(timeout=5)
        try:
            second = client.post(
                "/repository/load",
                json={"github_url": "https://github.com/example/other"},
            )
            assert second.status_code == 429
        finally:
            release_index.set()

        assert first_future.result(timeout=5).status_code == 200


@pytest.mark.parametrize("expiry_stage", ["before_index", "after_index"])
def test_repository_load_uses_one_budget_through_vector_index(
    monkeypatch, expiry_stage
):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    now = [0.0]
    budget = ImportBudget(
        max_requests=10,
        max_directories=10,
        timeout_seconds=5,
        clock=lambda: now[0],
    )
    monkeypatch.setattr(
        ImportBudget,
        "from_settings",
        classmethod(lambda cls: budget),
    )
    active_budgets = []
    index_calls = []

    def fake_load(_url):
        active_budgets.append(repo_loader._resolve_import_budget())
        if expiry_stage == "before_index":
            now[0] = 5.0
        return "example-demo-123", [], 1

    def fake_index(*_args):
        index_calls.append(True)
        active_budgets.append(repo_loader._resolve_import_budget())
        if expiry_stage == "after_index":
            now[0] = 5.0
        return _index_result()

    monkeypatch.setattr("app.main.load_repository", fake_load)
    monkeypatch.setattr("app.main.index_chunks_incremental", fake_index)

    with TestClient(app) as client:
        response = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "repository import deadline exceeded"}
    assert active_budgets and all(item is budget for item in active_budgets)
    assert bool(index_calls) is (expiry_stage == "after_index")


@pytest.mark.parametrize("failure_stage", ["loader", "vector"])
def test_repository_import_slot_is_released_after_failures(monkeypatch, failure_stage):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)

    if failure_stage == "loader":
        monkeypatch.setattr(
            "app.main.load_repository",
            lambda _url: (_ for _ in ()).throw(RepositoryLoadError("repository load failed")),
        )
        expected_status = 400
    else:
        monkeypatch.setattr("app.main.load_repository", lambda _url: ("example-demo-123", [], 1))
        monkeypatch.setattr(
            "app.main.index_chunks_incremental",
            lambda *_args: (_ for _ in ()).throw(VectorStoreError("vector write failed")),
        )
        expected_status = 500

    with TestClient(app) as client:
        failed = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )
        assert failed.status_code == expected_status

        monkeypatch.setattr("app.main.load_repository", lambda _url: ("example-demo-123", [], 1))
        monkeypatch.setattr("app.main.index_chunks_incremental", lambda *_args: _index_result())
        retried = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )

    assert retried.status_code == 200
