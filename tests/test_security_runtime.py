import json
import hashlib
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import app.main as main_module
from app.config import get_settings
from app.main import app
from app.security import auth
from app.services.repo_loader import RepositoryLoadError
from app.services.repository_catalog import RepositoryDeletionResult, RepositoryNotFoundError
from app.services.vector_store import IndexResult


def _request(host: str, *, path: str = "/repositories") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "client": (host, 12345),
            "server": ("testserver", 80),
        }
    )


def _configure_open_local(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "deployment_mode", "local", raising=False)
    monkeypatch.setattr(settings, "admin_username", "", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", "", raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "", raising=False)
    monkeypatch.setattr(settings, "app_api_key", "", raising=False)
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0, raising=False)
    monkeypatch.setattr(settings, "security_audit_enabled", True, raising=False)
    monkeypatch.setattr(settings, "security_audit_log_path", tmp_path / "audit.jsonl", raising=False)
    return settings


def _read_audit(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_auth_and_business_responses_are_not_cached_but_health_is_unchanged(monkeypatch, tmp_path):
    _configure_open_local(monkeypatch, tmp_path)

    with TestClient(app) as client:
        auth_status = client.get("/auth/status")
        repositories = client.get("/repositories")
        health = client.get("/health")

    assert auth_status.headers["cache-control"] == "no-store"
    assert repositories.headers["cache-control"] == "no-store"
    assert "no-store" not in health.headers.get("cache-control", "")


def test_business_rate_limit_bucket_map_has_hard_cap_and_prunes_stale_entries(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_max_requests", 2, raising=False)
    monkeypatch.setattr(settings, "rate_limit_window_seconds", 10, raising=False)
    monkeypatch.setattr(settings, "rate_limit_max_buckets", 2, raising=False)
    main_module._rate_limit_buckets.clear()
    main_module._rate_limit_buckets["stale"] = deque([1.0])
    monkeypatch.setattr(main_module.time, "monotonic", lambda: 100.0)

    first = _request("192.0.2.1")
    second = _request("192.0.2.2")
    rotating_attacker = _request("192.0.2.3")
    main_module.enforce_rate_limit(first)
    main_module.enforce_rate_limit(second)
    with pytest.raises(HTTPException) as blocked:
        main_module.enforce_rate_limit(rotating_attacker)
    main_module.enforce_rate_limit(first)
    with pytest.raises(HTTPException, match="rate limit exceeded"):
        main_module.enforce_rate_limit(first)

    assert "stale" not in main_module._rate_limit_buckets
    assert blocked.value.status_code == 429
    assert len(main_module._rate_limit_buckets) == 2


def test_login_attempt_bucket_map_has_hard_cap_and_prunes_stale_entries(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", 5, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 10, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_buckets", 2, raising=False)
    auth.clear_login_attempts()
    auth._login_attempts["stale"] = deque([1.0])
    monkeypatch.setattr(auth.time, "monotonic", lambda: 100.0)

    for index in range(5):
        auth.record_failed_login(_request(f"198.51.100.{index}", path="/auth/login"))

    assert "stale" not in auth._login_attempts
    assert len(auth._login_attempts) <= 3


def test_login_rate_limit_check_alone_cannot_create_more_than_bucket_cap(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", 2, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 10, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_buckets", 2, raising=False)
    auth.clear_login_attempts()
    monkeypatch.setattr(auth.time, "monotonic", lambda: 100.0)
    first = _request("203.0.113.1", path="/auth/login")
    second = _request("203.0.113.2", path="/auth/login")
    auth.record_failed_login(first)
    auth.record_failed_login(second)

    with pytest.raises(HTTPException) as blocked:
        auth.check_login_rate_limit(_request("203.0.113.3", path="/auth/login"))
    auth.check_login_rate_limit(first)
    auth.record_failed_login(first)
    with pytest.raises(HTTPException, match="too many login attempts"):
        auth.check_login_rate_limit(first)

    assert blocked.value.status_code == 429
    assert len(auth._login_attempts) == 2


def test_login_rate_limit_atomically_reserves_password_verification_slots(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", "test-hash", raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "session-secret-with-at-least-32-characters", raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", 2, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 60, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_buckets", 10, raising=False)
    monkeypatch.setattr(settings, "security_audit_enabled", False, raising=False)
    auth.clear_login_attempts()
    entered = 0
    entered_lock = Lock()
    two_entered = Event()
    release = Event()

    def slow_invalid_password(_password, _encoded_hash):
        nonlocal entered
        with entered_lock:
            entered += 1
            if entered == 2:
                two_entered.set()
        release.wait(timeout=5)
        return False

    monkeypatch.setattr(main_module, "verify_password", slow_invalid_password)

    def attempt(_index):
        request = _request("198.51.100.77", path="/auth/login")
        credentials = auth.LoginRequest(username="admin", password="wrong")
        try:
            main_module.auth_login(request, credentials)
        except HTTPException as exc:
            return exc.status_code
        return 200

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(attempt, index) for index in range(8)]
        assert two_entered.wait(timeout=5)
        release.set()
        statuses = [future.result(timeout=5) for future in futures]

    assert entered == 2
    assert statuses.count(401) == 2
    assert statuses.count(429) == 6


def test_successful_login_releases_its_atomic_reservation(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", "test-hash", raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "session-secret-with-at-least-32-characters", raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", 2, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 60, raising=False)
    monkeypatch.setattr(settings, "security_audit_enabled", False, raising=False)
    monkeypatch.setattr(main_module, "verify_password", lambda _password, _encoded_hash: True)
    auth.clear_login_attempts()

    for _index in range(3):
        response = main_module.auth_login(
            _request("203.0.113.88", path="/auth/login"),
            auth.LoginRequest(username="admin", password="correct"),
        )
        assert response.status_code == 200

    assert not auth._login_attempts
    assert not auth._login_in_flight


def test_login_success_and_failure_audit_is_minimal_and_secret_free(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    audit_path = settings.security_audit_log_path
    monkeypatch.setattr(settings, "admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", auth.hash_password("correct-password"), raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "session-secret-that-must-never-be-written", raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", 10, raising=False)
    auth.clear_login_attempts()

    with TestClient(app) as client:
        failed = client.post("/auth/login", json={"username": "admin", "password": "wrong-password-secret"})
        succeeded = client.post("/auth/login", json={"username": "admin", "password": "correct-password"})

    events = _read_audit(audit_path)
    assert failed.status_code == 401
    assert succeeded.status_code == 200
    assert [(event["event"], event["outcome"]) for event in events] == [
        ("login", "failure"),
        ("login", "success"),
    ]
    serialized = audit_path.read_text(encoding="utf-8")
    assert "wrong-password-secret" not in serialized
    assert "correct-password" not in serialized
    assert "session-secret" not in serialized
    assert set(events[0]) <= {"timestamp", "event", "outcome", "actor_id", "repository_fingerprint"}


def test_repository_import_success_and_failure_are_audited_without_request_or_source(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    audit_path = settings.security_audit_log_path
    index_result = IndexResult(
        chunks_indexed=1,
        files_indexed=1,
        chunks_written=1,
        index_cached=False,
        changed_files_count=1,
        removed_files_count=0,
    )
    secret_source = "DATABASE_URL=postgres://secret-user:secret-pass@db/private"
    monkeypatch.setattr(main_module, "load_repository", lambda _url: ("owner-repo", [secret_source], 1))
    monkeypatch.setattr(main_module, "index_chunks_incremental", lambda _repository_id, _chunks: index_result)

    source_ip = "198.51.100.42"
    with TestClient(app, client=(source_ip, 50000)) as client:
        succeeded = client.post("/repository/load", json={"github_url": "https://github.com/owner/repo"})

    def fail_load(_url):
        raise RepositoryLoadError("private upstream token ghp_super_secret")

    monkeypatch.setattr(main_module, "load_repository", fail_load)
    with TestClient(app, client=(source_ip, 50000)) as client:
        failed = client.post("/repository/load", json={"github_url": "https://github.com/owner/private"})

    events = _read_audit(audit_path)
    assert succeeded.status_code == 200
    assert failed.status_code == 400
    assert [(event["event"], event["outcome"]) for event in events] == [
        ("repository_import", "success"),
        ("repository_import", "failure"),
    ]
    assert events[0]["repository_fingerprint"] == hashlib.sha256(b"owner-repo").hexdigest()
    assert all(event.get("actor_id") for event in events)
    serialized = audit_path.read_text(encoding="utf-8")
    assert source_ip not in serialized
    assert secret_source not in serialized
    assert "ghp_super_secret" not in serialized
    assert "github.com/owner/private" not in serialized


def test_repository_delete_success_and_failure_are_audited_without_exception_details(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    audit_path = settings.security_audit_log_path
    monkeypatch.setattr(
        main_module.repository_catalog_service,
        "delete_repository",
        lambda repository_id: RepositoryDeletionResult(repository_id, True, 1),
    )
    secret_repository_id = "ghp_SUPERSECRETTOKEN"

    source_ip = "203.0.113.42"
    with TestClient(app, client=(source_ip, 50000)) as client:
        succeeded = client.delete(f"/repositories/{secret_repository_id}")

    def fail_delete(_repository_id):
        raise RepositoryNotFoundError("C:\\private\\repository-token")

    monkeypatch.setattr(main_module.repository_catalog_service, "delete_repository", fail_delete)
    with TestClient(app, client=(source_ip, 50000)) as client:
        failed = client.delete("/repositories/missing-repo")

    def crash_delete(_repository_id):
        raise OSError("C:\\private\\unexpected-delete-secret")

    monkeypatch.setattr(main_module.repository_catalog_service, "delete_repository", crash_delete)
    with TestClient(app, client=(source_ip, 50000), raise_server_exceptions=False) as client:
        crashed = client.delete("/repositories/broken-repo")

    events = _read_audit(audit_path)
    assert succeeded.status_code == 200
    assert failed.status_code == 404
    assert crashed.status_code == 500
    assert crashed.json() == {"detail": "repository deletion failed"}
    assert [(event["event"], event["outcome"]) for event in events] == [
        ("repository_delete", "success"),
        ("repository_delete", "failure"),
        ("repository_delete", "failure"),
    ]
    expected_fingerprint = hashlib.sha256(secret_repository_id.encode("utf-8")).hexdigest()
    assert events[0]["repository_fingerprint"] == expected_fingerprint
    assert len(events[0]["repository_fingerprint"]) == 64
    assert all(event.get("actor_id") for event in events)
    serialized = audit_path.read_text(encoding="utf-8")
    assert source_ip not in serialized
    assert secret_repository_id not in serialized
    assert "repository-token" not in serialized
    assert "unexpected-delete-secret" not in serialized


def test_https_redirects_receive_business_cache_policy_and_security_headers(monkeypatch, tmp_path):
    settings = _configure_open_local(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "force_https", True, raising=False)
    monkeypatch.setattr(settings, "public_base_url", "https://code.example.com", raising=False)

    with TestClient(app, base_url="http://localhost", follow_redirects=False) as client:
        response = client.get("/auth/status")

    assert response.status_code == 307
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_runtime_security_settings_are_documented_and_jsonl_multiprocess_limit_is_explicit():
    env_example = open(".env.example", encoding="utf-8").read()
    readme = open("README.md", encoding="utf-8").read()
    security_log = open("SECURITY_LOG.md", encoding="utf-8").read()

    for name in (
        "LOGIN_RATE_LIMIT_MAX_BUCKETS",
        "RATE_LIMIT_MAX_BUCKETS",
        "SECURITY_AUDIT_ENABLED",
        "SECURITY_AUDIT_LOG_PATH",
    ):
        assert f"{name}=" in env_example
    assert "JSONL" in readme and "多进程" in readme and "交错" in readme
    assert "JSONL" in security_log and "多进程" in security_log and "交错" in security_log
