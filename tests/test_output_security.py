from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import get_settings
from app.main import app
from app.services import llm_service
from app.services.llm_service import LLMServiceError
from app.services.repo_loader import RepositoryLoadError
from app.services.vector_store import VectorStoreError


SENTINEL = r"TOP-SECRET-C:\private\upstream.txt"
SECRET_VALUES = (
    SENTINEL,
    "token-value-that-must-not-leak",
    "password-value-that-must-not-leak",
    "bearer-value-that-must-not-leak",
    "db-user:db-password@internal.example/private",
)
SENSITIVE_CONTENT = "\n".join(
    [
        f"API_KEY={SENTINEL}",
        f"access_token={SECRET_VALUES[1]}",
        f"password={SECRET_VALUES[2]}",
        f"Authorization: Bearer {SECRET_VALUES[3]}",
        f"DATABASE_URL=postgresql://{SECRET_VALUES[4]}",
    ]
)


def _chunk(content: str = SENSITIVE_CONTENT) -> dict:
    return {
        "content": content,
        "metadata": {
            "file_path": "app/settings.py",
            "chunk_index": 0,
            "start_line": 1,
            "end_line": 5,
            "language": "python",
            "symbol_name": "settings",
            "symbol_type": "module",
        },
        "distance": 0.1,
        "retrieval_rank": 1,
        "rerank_score": 5.0,
    }


def _configure_routes(monkeypatch, *, api_key: str = "", debug: bool = False) -> None:
    settings = get_settings()
    values = {
        "deployment_mode": "local",
        "app_api_key": api_key,
        "admin_username": "",
        "admin_password_hash": "",
        "auth_session_secret": "",
        "force_https": False,
        "tls_terminated_by_proxy": False,
        "rate_limit_max_requests": 0,
        "enable_debug_routes": debug,
    }
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def _assert_stable_error(response, expected_detail: str) -> None:
    assert response.status_code == 500
    assert response.json() == {"detail": expected_detail}
    assert "TOP-SECRET" not in response.text


def _assert_no_secret_logged(caplog) -> None:
    for secret in SECRET_VALUES:
        assert secret not in caplog.text


def test_llm_rerank_redacts_candidate_preview_before_external_call(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "enable_llm_rerank", True)
    monkeypatch.setattr(settings, "deepseek_api_key", "server-side-test-key")
    captured: dict = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            message = SimpleNamespace(content='[{"index": 1, "score": 9}]')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm_service, "get_llm_client", lambda: client)

    llm_service.llm_rerank_chunks("Where is configuration loaded?", [_chunk()])

    outbound = captured["messages"][1]["content"]
    assert "[REDACTED]" in outbound
    for secret in SECRET_VALUES:
        assert secret not in outbound


def test_debug_retrieval_redacts_preview_for_authenticated_request(monkeypatch):
    api_key = "a" * 32
    _configure_routes(monkeypatch, api_key=api_key, debug=True)
    monkeypatch.setattr(main, "build_retrieval_queries", lambda _question: ["configuration"])
    monkeypatch.setattr(main, "retrieve_relevant_chunks", lambda *_args: [_chunk()])
    monkeypatch.setattr(main, "rerank_chunks", lambda _question, chunks: chunks)

    with TestClient(app) as client:
        response = client.post(
            "/debug/retrieval",
            headers={"X-API-Key": api_key},
            json={"repository_id": "demo", "question": "configuration"},
        )

    assert response.status_code == 200
    preview = response.json()["chunks"][0]["preview"]
    assert "[REDACTED]" in preview
    assert "TOP-SECRET" not in response.text
    for secret in SECRET_VALUES:
        assert secret not in preview


def test_answer_question_wraps_upstream_error_without_raw_detail(monkeypatch, caplog):
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "server-side-test-key")

    class FailingCompletions:
        def create(self, **_kwargs):
            raise RuntimeError(SENTINEL)

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FailingCompletions())

    monkeypatch.setattr(llm_service, "OpenAI", FakeOpenAI)

    with pytest.raises(LLMServiceError) as exc_info:
        llm_service.answer_question("Explain the project", [_chunk("safe evidence")])

    assert str(exc_info.value) == "answer generation failed"
    assert "TOP-SECRET" not in str(exc_info.value)
    _assert_no_secret_logged(caplog)


def test_chat_llm_error_has_stable_public_message(monkeypatch):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(main.rag_agent, "search", lambda *_args: ([_chunk("safe evidence")], []))
    monkeypatch.setattr(
        main,
        "answer_question",
        lambda *_args: (_ for _ in ()).throw(LLMServiceError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post("/chat", json={"repository_id": "demo", "question": "explain"})

    _assert_stable_error(response, "answer generation failed")


def test_chat_vector_error_has_stable_public_message(monkeypatch):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(
        main.rag_agent,
        "search",
        lambda *_args: (_ for _ in ()).throw(VectorStoreError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post("/chat", json={"repository_id": "demo", "question": "explain"})

    _assert_stable_error(response, "vector store operation failed")


def test_repository_import_error_does_not_expose_loader_detail(monkeypatch, caplog):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(
        main,
        "load_repository",
        lambda _url: (_ for _ in ()).throw(RepositoryLoadError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )

    assert response.status_code == 400
    assert response.json() == {"detail": "repository import failed"}
    assert "TOP-SECRET" not in response.text
    _assert_no_secret_logged(caplog)


def test_repository_import_unexpected_error_does_not_log_raw_detail(monkeypatch, caplog):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(
        main,
        "load_repository",
        lambda _url: (_ for _ in ()).throw(RuntimeError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )

    _assert_stable_error(response, "repository import failed")
    assert "RuntimeError" in caplog.text
    _assert_no_secret_logged(caplog)


def test_chat_unexpected_error_does_not_log_raw_detail(monkeypatch, caplog):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(
        main.rag_agent,
        "search",
        lambda *_args: (_ for _ in ()).throw(RuntimeError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post("/chat", json={"repository_id": "demo", "question": "explain"})

    _assert_stable_error(response, "code question failed")
    assert "RuntimeError" in caplog.text
    _assert_no_secret_logged(caplog)


def test_repository_import_vector_error_has_stable_public_message(monkeypatch):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(main, "load_repository", lambda _url: ("example-demo-123", [], 1))
    monkeypatch.setattr(
        main,
        "index_chunks_incremental",
        lambda *_args: (_ for _ in ()).throw(VectorStoreError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/repository/load",
            json={"github_url": "https://github.com/example/demo"},
        )

    _assert_stable_error(response, "vector store operation failed")


def test_repository_delete_vector_error_has_stable_public_message(monkeypatch):
    _configure_routes(monkeypatch)
    monkeypatch.setattr(
        main.repository_catalog_service,
        "delete_repository",
        lambda _repository_id: (_ for _ in ()).throw(VectorStoreError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.delete("/repositories/demo")

    _assert_stable_error(response, "vector store operation failed")


def test_debug_retrieval_vector_error_has_stable_public_message(monkeypatch):
    _configure_routes(monkeypatch, debug=True)
    monkeypatch.setattr(main, "build_retrieval_queries", lambda _question: ["configuration"])
    monkeypatch.setattr(
        main,
        "retrieve_relevant_chunks",
        lambda *_args: (_ for _ in ()).throw(VectorStoreError(SENTINEL)),
    )

    with TestClient(app) as client:
        response = client.post(
            "/debug/retrieval",
            json={"repository_id": "demo", "question": "configuration"},
        )

    _assert_stable_error(response, "vector store operation failed")
