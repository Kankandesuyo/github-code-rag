from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from threading import Lock

from fastapi import Request

from app.config import get_settings


logger = logging.getLogger(__name__)
_audit_lock = Lock()
_ALLOWED_EVENTS = frozenset({"login", "repository_import", "repository_delete"})
_ALLOWED_OUTCOMES = frozenset({"success", "failure"})
_SAFE_REPOSITORY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,140}$")


def _actor_id(request: Request | None) -> str | None:
    if request is None:
        return None
    host = request.client.host if request.client else "unknown"
    return hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]


def write_security_audit(
    event: str,
    outcome: str,
    *,
    request: Request | None = None,
    repository_id: str | None = None,
) -> None:
    if event not in _ALLOWED_EVENTS or outcome not in _ALLOWED_OUTCOMES:
        raise ValueError("unsupported security audit event")

    settings = get_settings()
    if not settings.security_audit_enabled:
        return

    record: dict[str, str] = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "outcome": outcome,
    }
    actor_id = _actor_id(request)
    if actor_id is not None:
        record["actor_id"] = actor_id
    if repository_id is not None and _SAFE_REPOSITORY_ID.fullmatch(repository_id):
        record["repository_fingerprint"] = hashlib.sha256(
            repository_id.encode("utf-8")
        ).hexdigest()

    path = settings.security_audit_log_path
    try:
        with _audit_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True))
                handle.write("\n")
    except OSError as exc:
        logger.error("security audit write failed error_type=%s", type(exc).__name__)
