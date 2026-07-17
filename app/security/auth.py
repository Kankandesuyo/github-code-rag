import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from threading import Lock
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings


SESSION_COOKIE_NAME = "github_code_rag_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_in_flight: dict[str, dict[str, float]] = defaultdict(dict)
_login_attempts_lock = Lock()
_LOGIN_RESERVATION_STATE_KEY = "login_rate_limit_reservation"


class AuthConfigurationError(RuntimeError):
    pass


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=1024)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    n, r, p = 2**14, 8, 1
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt:{n}:{r}:{p}:{_b64encode(salt)}:{_b64encode(digest)}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded_hash.split(":", 5)
        if algorithm != "scrypt":
            return False
        n, r, p = int(n_text), int(r_text), int(p_text)
        if n > 2**18 or r > 16 or p > 4:
            return False
        expected = _b64decode(digest_text)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(salt_text),
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def is_auth_enabled() -> bool:
    settings = get_settings()
    return bool(settings.admin_username.strip() or settings.admin_password_hash.strip())


def ensure_auth_ready() -> None:
    if not is_auth_enabled():
        return
    settings = get_settings()
    if not settings.admin_username.strip() or not settings.admin_password_hash.strip():
        raise AuthConfigurationError("ADMIN_USERNAME and ADMIN_PASSWORD_HASH must both be configured")
    if len(settings.auth_session_secret.strip()) < 32:
        raise AuthConfigurationError("AUTH_SESSION_SECRET must contain at least 32 characters")


def ensure_deployment_security() -> None:
    settings = get_settings()
    deployment_mode = settings.deployment_mode.strip().lower()
    if deployment_mode not in {"local", "development", "production"}:
        raise AuthConfigurationError(
            "DEPLOYMENT_MODE must be one of: local, development, production"
        )
    if deployment_mode != "production":
        return

    public_base_url = settings.public_base_url.strip()
    try:
        parsed_base_url = urlsplit(public_base_url)
        parsed_base_url.port
    except ValueError as exc:
        raise AuthConfigurationError("PUBLIC_BASE_URL must be a valid HTTPS URL") from exc
    if (
        parsed_base_url.scheme.lower() != "https"
        or not parsed_base_url.hostname
        or parsed_base_url.username is not None
        or parsed_base_url.password is not None
        or parsed_base_url.query
        or parsed_base_url.fragment
        or any(character.isspace() for character in public_base_url)
    ):
        raise AuthConfigurationError(
            "PUBLIC_BASE_URL must be an HTTPS URL without credentials, query, or fragment"
        )

    if any(host.strip() == "*" for host in settings.allowed_hosts):
        raise AuthConfigurationError("ALLOWED_HOSTS must not contain '*' in production")
    if not settings.force_https and not settings.tls_terminated_by_proxy:
        raise AuthConfigurationError(
            "Production requires FORCE_HTTPS=true or TLS_TERMINATED_BY_PROXY=true"
        )

    app_api_key = settings.app_api_key.strip()
    if app_api_key and len(app_api_key) < 32:
        raise AuthConfigurationError(
            "APP_API_KEY must contain at least 32 characters in production"
        )

    admin_username = settings.admin_username.strip()
    admin_password_hash = settings.admin_password_hash.strip()
    auth_session_secret = settings.auth_session_secret.strip()
    admin_values = (admin_username, admin_password_hash, auth_session_secret)
    if any(admin_values):
        if not all(admin_values):
            raise AuthConfigurationError(
                "ADMIN_USERNAME, ADMIN_PASSWORD_HASH, and AUTH_SESSION_SECRET must all be configured"
            )
        ensure_auth_ready()
        if not settings.auth_cookie_secure:
            raise AuthConfigurationError(
                "AUTH_COOKIE_SECURE must be true when administrator sessions are enabled in production"
            )
        return

    if not app_api_key:
        raise AuthConfigurationError(
            "Production deployment requires authentication via administrator credentials or APP_API_KEY"
        )


def create_session_token(username: str, *, now: int | None = None, ttl_seconds: int | None = None) -> tuple[str, str]:
    ensure_auth_ready()
    settings = get_settings()
    issued_at = int(time.time()) if now is None else int(now)
    ttl = settings.auth_session_ttl_seconds if ttl_seconds is None else ttl_seconds
    csrf_token = secrets.token_urlsafe(24)
    payload = {
        "sub": username,
        "iat": issued_at,
        "exp": issued_at + int(ttl),
        "csrf": csrf_token,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(
        settings.auth_session_secret.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_b64encode(signature)}", csrf_token


def decode_session_token(token: str, *, now: int | None = None) -> dict | None:
    try:
        ensure_auth_ready()
        encoded_payload, encoded_signature = token.split(".", 1)
        settings = get_settings()
        expected = hmac.new(
            settings.auth_session_secret.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(encoded_signature)):
            return None
        payload = json.loads(_b64decode(encoded_payload).decode("utf-8"))
        current_time = int(time.time()) if now is None else int(now)
        if int(payload.get("exp", 0)) < current_time:
            return None
        if payload.get("sub") != settings.admin_username.strip() or not payload.get("csrf"):
            return None
        return payload
    except (AuthConfigurationError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def authorize_request(request: Request, x_api_key: str | None, csrf_token: str | None = None) -> dict | None:
    settings = get_settings()
    expected_api_key = settings.app_api_key.strip()
    if expected_api_key and x_api_key and secrets.compare_digest(x_api_key, expected_api_key):
        return None

    if not is_auth_enabled():
        if expected_api_key:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return None

    try:
        ensure_auth_ready()
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    session = decode_session_token(request.cookies.get(SESSION_COOKIE_NAME, ""))
    if session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if request.method.upper() not in SAFE_METHODS:
        supplied = csrf_token or ""
        if not supplied or not secrets.compare_digest(supplied, str(session["csrf"])):
            raise HTTPException(status_code=403, detail="invalid or missing CSRF token")
    return session


def login_bucket_key(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode("utf-8")).hexdigest()


def check_login_rate_limit(request: Request) -> None:
    settings = get_settings()
    limit = settings.login_rate_limit_max_attempts
    window = settings.login_rate_limit_window_seconds
    if limit <= 0 or window <= 0:
        return
    now = time.monotonic()
    cutoff = now - window
    key = login_bucket_key(request)
    with _login_attempts_lock:
        _prune_login_attempts(cutoff)
        known_keys = set(_login_attempts) | set(_login_in_flight)
        if key not in known_keys and len(known_keys) >= settings.login_rate_limit_max_buckets:
            raise HTTPException(status_code=429, detail="login rate limit capacity reached")
        failures = _login_attempts.get(key)
        in_flight = _login_in_flight.get(key)
        if len(failures or ()) + len(in_flight or ()) >= limit:
            raise HTTPException(status_code=429, detail="too many login attempts")
        reservation = secrets.token_urlsafe(18)
        _login_in_flight.setdefault(key, {})[reservation] = now
        setattr(request.state, _LOGIN_RESERVATION_STATE_KEY, (key, reservation))


def record_failed_login(request: Request) -> None:
    settings = get_settings()
    now = time.monotonic()
    with _login_attempts_lock:
        _prune_login_attempts(now - settings.login_rate_limit_window_seconds)
        key = login_bucket_key(request)
        reservation_data = getattr(request.state, _LOGIN_RESERVATION_STATE_KEY, None)
        if reservation_data is not None:
            reserved_key, reservation = reservation_data
            reservations = _login_in_flight.get(reserved_key)
            if reservations is not None and reservation in reservations:
                reservations.pop(reservation, None)
                if not reservations:
                    _login_in_flight.pop(reserved_key, None)
                _login_attempts.setdefault(reserved_key, deque()).append(now)
                setattr(request.state, _LOGIN_RESERVATION_STATE_KEY, None)
                return

        known_keys = set(_login_attempts) | set(_login_in_flight)
        if key not in known_keys and len(known_keys) >= settings.login_rate_limit_max_buckets:
            return
        _login_attempts.setdefault(key, deque()).append(now)


def _prune_login_attempts(cutoff: float) -> None:
    for key, attempts in list(_login_attempts.items()):
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if not attempts:
            _login_attempts.pop(key, None)
    for key, reservations in list(_login_in_flight.items()):
        for reservation, started_at in list(reservations.items()):
            if started_at < cutoff:
                reservations.pop(reservation, None)
        if not reservations:
            _login_in_flight.pop(key, None)


def clear_login_attempts(request: Request | None = None) -> None:
    with _login_attempts_lock:
        if request is None:
            _login_attempts.clear()
            _login_in_flight.clear()
        else:
            key = login_bucket_key(request)
            _login_attempts.pop(key, None)
            reservation_data = getattr(request.state, _LOGIN_RESERVATION_STATE_KEY, None)
            if reservation_data is None:
                _login_in_flight.pop(key, None)
                return
            reserved_key, reservation = reservation_data
            reservations = _login_in_flight.get(reserved_key)
            if reservations is not None:
                reservations.pop(reservation, None)
                if not reservations:
                    _login_in_flight.pop(reserved_key, None)
            setattr(request.state, _LOGIN_RESERVATION_STATE_KEY, None)
