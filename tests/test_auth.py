from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.security import auth


def configure_admin(monkeypatch, *, max_attempts=5):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_username", "admin", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", auth.hash_password("correct-password"), raising=False)
    monkeypatch.setattr(settings, "auth_session_secret", "test-session-secret-with-enough-entropy", raising=False)
    monkeypatch.setattr(settings, "auth_session_ttl_seconds", 3600, raising=False)
    monkeypatch.setattr(settings, "auth_cookie_secure", False, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_window_seconds", 60, raising=False)
    monkeypatch.setattr(settings, "login_rate_limit_max_attempts", max_attempts, raising=False)
    monkeypatch.setattr(settings, "app_api_key", "automation-key")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)
    auth.clear_login_attempts()
    return settings


def test_auth_disabled_keeps_local_development_open(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "admin_username", "", raising=False)
    monkeypatch.setattr(settings, "admin_password_hash", "", raising=False)
    monkeypatch.setattr(settings, "app_api_key", "")
    monkeypatch.setattr(settings, "rate_limit_max_requests", 0)

    with TestClient(app) as client:
        status = client.get("/auth/status")
        repositories = client.get("/repositories")

    assert status.json() == {
        "enabled": False,
        "authenticated": True,
        "username": None,
        "csrf_token": None,
    }
    assert repositories.status_code == 200


def test_password_hash_is_env_file_safe_and_verifiable():
    encoded = auth.hash_password("strong-password")

    assert "$" not in encoded
    assert auth.verify_password("strong-password", encoded) is True
    assert auth.verify_password("wrong-password", encoded) is False


def test_login_sets_signed_httponly_session_and_unlocks_routes(monkeypatch):
    configure_admin(monkeypatch)

    with TestClient(app) as client:
        before = client.get("/repositories")
        login = client.post("/auth/login", json={"username": "admin", "password": "correct-password"})
        after = client.get("/repositories")

    assert before.status_code == 401
    assert login.status_code == 200
    assert login.json()["authenticated"] is True
    assert login.json()["csrf_token"]
    assert "HttpOnly" in login.headers["set-cookie"]
    assert "SameSite=strict" in login.headers["set-cookie"]
    assert after.status_code == 200


def test_invalid_login_and_tampered_cookie_are_rejected(monkeypatch):
    configure_admin(monkeypatch)

    with TestClient(app) as client:
        invalid = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        client.cookies.set(auth.SESSION_COOKIE_NAME, "tampered.payload")
        protected = client.get("/repositories")

    assert invalid.status_code == 401
    assert protected.status_code == 401


def test_session_expiry_is_enforced(monkeypatch):
    configure_admin(monkeypatch)
    token, _csrf = auth.create_session_token("admin", now=100, ttl_seconds=5)

    assert auth.decode_session_token(token, now=104)["sub"] == "admin"
    assert auth.decode_session_token(token, now=106) is None


def test_session_logout_requires_csrf(monkeypatch):
    configure_admin(monkeypatch)

    with TestClient(app) as client:
        login = client.post("/auth/login", json={"username": "admin", "password": "correct-password"})
        csrf_token = login.json()["csrf_token"]
        missing = client.post("/auth/logout")
        accepted = client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})
        after = client.get("/repositories")

    assert missing.status_code == 403
    assert accepted.status_code == 200
    assert after.status_code == 401


def test_api_key_remains_valid_for_automation(monkeypatch):
    configure_admin(monkeypatch)

    with TestClient(app) as client:
        response = client.get("/repositories", headers={"X-API-Key": "automation-key"})

    assert response.status_code == 200


def test_login_rate_limit_blocks_repeated_failures(monkeypatch):
    configure_admin(monkeypatch, max_attempts=2)

    with TestClient(app) as client:
        first = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        second = client.post("/auth/login", json={"username": "admin", "password": "wrong"})
        third = client.post("/auth/login", json={"username": "admin", "password": "correct-password"})

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429


def test_frontend_uses_session_login_without_local_storage_secrets():
    html = Path("app/static/index.html")
    javascript = html.with_name("app.js")

    html_content = html.read_text(encoding="utf-8")
    script_content = javascript.read_text(encoding="utf-8")

    assert 'id="loginForm"' in html_content
    assert 'id="logoutButton"' in html_content
    assert "localStorage" not in script_content
    assert 'fetch("/auth/status"' in script_content
    assert 'fetch("/auth/login"' in script_content
    assert 'fetch("/auth/logout"' in script_content
