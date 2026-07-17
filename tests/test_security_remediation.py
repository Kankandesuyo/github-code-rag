import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.security.auth import AuthConfigurationError


STRONG_API_KEY = "a" * 32


def configure_security(
    monkeypatch,
    *,
    deployment_mode: str = "local",
    app_api_key: str = "",
    admin_username: str = "",
    admin_password_hash: str = "",
    auth_session_secret: str = "",
    auth_cookie_secure: bool = False,
    force_https: bool = False,
    tls_terminated_by_proxy: bool = False,
    public_base_url: str = "https://localhost",
    allowed_hosts: list[str] | None = None,
):
    settings = get_settings()
    values = {
        "deployment_mode": deployment_mode,
        "app_api_key": app_api_key,
        "admin_username": admin_username,
        "admin_password_hash": admin_password_hash,
        "auth_session_secret": auth_session_secret,
        "auth_cookie_secure": auth_cookie_secure,
        "force_https": force_https,
        "tls_terminated_by_proxy": tls_terminated_by_proxy,
        "public_base_url": public_base_url,
        "rate_limit_max_requests": 0,
    }
    if allowed_hosts is not None:
        values["allowed_hosts"] = allowed_hosts
    for name, value in values.items():
        monkeypatch.setitem(settings.__dict__, name, value)
    return settings


def test_allowed_hosts_parses_comma_separated_values_robustly(monkeypatch):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    defaults = Settings(_env_file=None)
    monkeypatch.setenv(
        "ALLOWED_HOSTS",
        " localhost, 127.0.0.1 ,, testserver,localhost ",
    )
    parsed = Settings(_env_file=None)

    assert getattr(defaults, "allowed_hosts", None) == ["localhost", "127.0.0.1", "testserver"]
    assert getattr(parsed, "allowed_hosts", None) == ["localhost", "127.0.0.1", "testserver"]


def test_deployment_mode_defaults_to_local_without_environment_override(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("TLS_TERMINATED_BY_PROXY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.deployment_mode == "local"
    assert getattr(settings, "tls_terminated_by_proxy", None) is False


def test_local_mode_without_authentication_remains_open(monkeypatch):
    configure_security(monkeypatch)

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


def test_production_startup_fails_without_any_authentication(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        tls_terminated_by_proxy=True,
    )

    with pytest.raises(AuthConfigurationError, match="authentication"):
        with TestClient(app):
            pass


def test_unknown_deployment_mode_fails_closed(monkeypatch):
    configure_security(monkeypatch, deployment_mode="prodution", app_api_key="automation-secret")

    with pytest.raises(AuthConfigurationError, match="DEPLOYMENT_MODE"):
        with TestClient(app):
            pass


def test_production_startup_rejects_partial_admin_configuration_even_with_api_key(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        admin_username="admin",
        tls_terminated_by_proxy=True,
    )

    with pytest.raises(AuthConfigurationError, match="ADMIN_USERNAME"):
        with TestClient(app):
            pass


def test_production_session_authentication_requires_secure_cookie(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        admin_username="admin",
        admin_password_hash="configured-password-hash",
        auth_session_secret="a-production-session-secret-with-32-chars",
        auth_cookie_secure=False,
        tls_terminated_by_proxy=True,
    )

    with pytest.raises(AuthConfigurationError, match="AUTH_COOKIE_SECURE"):
        with TestClient(app):
            pass


def test_complete_production_admin_authentication_starts_with_secure_cookie(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        admin_username="admin",
        admin_password_hash="configured-password-hash",
        auth_session_secret="a-production-session-secret-with-32-chars",
        auth_cookie_secure=True,
        tls_terminated_by_proxy=True,
    )

    with TestClient(app) as client:
        response = client.get("/auth/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is True


@pytest.mark.parametrize("weak_api_key", ["x", "short-automation-secret"])
def test_production_rejects_weak_api_key(monkeypatch, weak_api_key):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=weak_api_key,
        tls_terminated_by_proxy=True,
    )

    with pytest.raises(AuthConfigurationError, match="APP_API_KEY.*32"):
        with TestClient(app):
            pass


def test_api_key_only_production_accepts_32_character_key_without_session_secret(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        tls_terminated_by_proxy=True,
    )

    with TestClient(app) as client:
        rejected = client.get("/repositories")
        accepted = client.get("/repositories", headers={"X-API-Key": STRONG_API_KEY})

    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_local_mode_does_not_impose_production_api_key_length(monkeypatch):
    configure_security(monkeypatch, app_api_key="short")

    with TestClient(app) as client:
        response = client.get("/repositories", headers={"X-API-Key": "short"})

    assert response.status_code == 200


def test_production_requires_application_or_proxy_https_enforcement(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        force_https=False,
        tls_terminated_by_proxy=False,
    )

    with pytest.raises(AuthConfigurationError, match="FORCE_HTTPS.*TLS_TERMINATED_BY_PROXY"):
        with TestClient(app):
            pass


def test_production_rejects_wildcard_allowed_hosts(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        tls_terminated_by_proxy=True,
        allowed_hosts=["*"],
    )

    with pytest.raises(AuthConfigurationError, match="ALLOWED_HOSTS"):
        with TestClient(app):
            pass


@pytest.mark.parametrize(
    "public_base_url",
    [
        "http://code.example.com",
        "https://user:password@code.example.com",
        "https://code.example.com?next=evil",
        "https://code.example.com#fragment",
    ],
)
def test_production_startup_rejects_unsafe_public_base_url(monkeypatch, public_base_url):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        tls_terminated_by_proxy=True,
        public_base_url=public_base_url,
    )

    with pytest.raises(AuthConfigurationError, match="PUBLIC_BASE_URL"):
        with TestClient(app):
            pass


def test_untrusted_host_is_rejected(monkeypatch):
    configure_security(
        monkeypatch,
        force_https=True,
        public_base_url="https://code.example.com",
    )

    with TestClient(app) as client:
        response = client.get("/health", headers={"Host": "attacker.example"})

    assert response.status_code == 400


def test_https_redirect_uses_canonical_base_and_preserves_path_and_query(monkeypatch):
    configure_security(
        monkeypatch,
        deployment_mode="production",
        app_api_key=STRONG_API_KEY,
        force_https=True,
        public_base_url="https://code.example.com/rag",
    )

    with TestClient(app, base_url="http://localhost:9876") as client:
        response = client.get("/nested/report?tab=security&item=1", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "https://code.example.com/rag/nested/report?tab=security&item=1"
    assert "localhost" not in response.headers["location"]
