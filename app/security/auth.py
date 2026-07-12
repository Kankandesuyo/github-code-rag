import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from app.config import get_settings


SESSION_COOKIE_NAME = "github_code_rag_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_attempts_lock = Lock()


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
        attempts = _login_attempts[key]
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= limit:
            raise HTTPException(status_code=429, detail="too many login attempts")


def record_failed_login(request: Request) -> None:
    with _login_attempts_lock:
        _login_attempts[login_bucket_key(request)].append(time.monotonic())


def clear_login_attempts(request: Request | None = None) -> None:
    with _login_attempts_lock:
        if request is None:
            _login_attempts.clear()
        else:
            _login_attempts.pop(login_bucket_key(request), None)
