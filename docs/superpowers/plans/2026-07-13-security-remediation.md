# Backend Security Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the verified deployment, Host-header, resource-exhaustion, document-parser, data-leakage, rate-limit, cache, audit, and supply-chain gaps without breaking local single-admin development.

**Architecture:** Introduce an explicit local/production deployment boundary and fail closed only in production. Keep security policy in focused helpers, enforce bounded repository traversal and imports, apply one redaction boundary before any external/debug output, and add low-risk structured audit events without logging secrets or source code.

**Tech Stack:** Python 3.12, FastAPI/Starlette middleware, Pydantic Settings, ChromaDB, pytest, Docker Compose, GitHub Actions.

**Status (2026-07-13):** Completed. All five tasks were implemented and independently verified; see `UPDATE_README.md` and `SECURITY_LOG.md` for evidence and the time-limited Chroma advisory waiver.

---

### Task 1: Production authentication and trusted hosts

**Files:**
- Modify: `app/config.py`
- Modify: `app/security/auth.py`
- Modify: `app/main.py`
- Modify: `.env.example`
- Modify: `compose.yaml`
- Test: `tests/test_security_remediation.py`

- [ ] Add tests proving local mode remains open, production mode without administrator credentials or API key raises `AuthConfigurationError`, untrusted Host returns 400, and HTTPS redirects use `PUBLIC_BASE_URL` instead of the request Host.
- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests/test_security_remediation.py -q` and confirm the tests fail because deployment mode, allowed hosts and canonical redirects do not exist.
- [ ] Add `deployment_mode`, `allowed_hosts`, and `public_base_url` settings. Implement `ensure_deployment_security()` which rejects production startup without auth and rejects insecure production cookies.
- [ ] Add `TrustedHostMiddleware`, call the deployment check during lifespan startup, and build HTTPS redirects from the validated `PUBLIC_BASE_URL`.
- [ ] Bind Compose to `127.0.0.1:8000:8000` by default and document explicit reverse-proxy/public configuration in `.env.example`.
- [ ] Re-run focused auth/security tests and confirm they pass.

### Task 2: Bounded repository imports and safe document parsing

**Files:**
- Modify: `app/config.py`
- Modify: `app/services/repo_loader.py`
- Modify: `app/main.py`
- Modify: `requirements.txt`
- Modify: `.env.example`
- Test: `tests/test_import_security.py`

- [ ] Add tests for maximum visited directories, maximum outbound requests, an overall monotonic deadline, and one concurrent import slot returning 429 for a second import.
- [ ] Run the focused tests and verify failures for missing budgets and semaphore.
- [ ] Add `MAX_REPOSITORY_DIRECTORIES`, `MAX_REPOSITORY_REQUESTS`, `REPOSITORY_IMPORT_TIMEOUT_SECONDS`, and `MAX_CONCURRENT_IMPORTS`; use an `ImportBudget` object at every GitHub web/API request boundary.
- [ ] Add a non-blocking process semaphore around `/repository/load`, release it in `finally`, and return 429 when capacity is exhausted.
- [ ] Upgrade `pypdf` to `>=6.0.0,<7.0.0` and preserve the existing per-file and total-byte filters.
- [ ] Run importer tests, `pip check`, and document-parser tests.

### Task 3: Redaction and stable error boundaries

**Files:**
- Modify: `app/services/llm_service.py`
- Modify: `app/main.py`
- Test: `tests/test_security_remediation.py`

- [ ] Add tests proving LLM rerank candidates and debug retrieval previews redact assignments, bearer tokens and database URLs, and upstream DeepSeek exception strings never reach API responses.
- [ ] Run focused tests and verify raw secret assertions fail.
- [ ] Apply `redact_sensitive_text()` to every external-model candidate and debug preview; replace LLM/Vector exception details with stable client messages while logging only operation and exception type.
- [ ] Run focused tests and existing RAG tests.

### Task 4: Durable process protections

**Files:**
- Create: `app/security/audit.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `app/security/auth.py`
- Test: `tests/test_security_remediation.py`

- [ ] Add tests for `Cache-Control: no-store` on auth and business responses, bounded rate-limit bucket cleanup, and JSON audit events for login success/failure, repository import and deletion without passwords, tokens or source content.
- [ ] Run focused tests and verify the missing header, unbounded bucket and missing audit file failures.
- [ ] Implement an append-only JSONL audit writer with a lock, controlled path, event allowlist, timestamp and sanitized identifiers.
- [ ] Add cache headers, prune empty/old rate buckets, cap bucket-map size, and emit audit events at the four tested boundaries.
- [ ] Run focused tests and full authentication/catalog regression tests.

### Task 5: CI, container hardening, documentation and verification

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `SECURITY_LOG.md`
- Modify: `UPDATE_README.md`
- Test: `tests/test_delivery_files.py`

- [ ] Add static tests requiring `pip-audit`, a pinned base-image digest or documented scanning stage, `no-new-privileges`, dropped capabilities, localhost default binding and production-security environment examples.
- [ ] Run delivery tests and verify new assertions fail.
- [ ] Add `pip-audit` CI execution, Compose `security_opt: no-new-privileges:true`, `cap_drop: ALL`, and bounded memory/CPU defaults that remain compatible with local embeddings.
- [ ] Document the local versus production threat model, reverse-proxy body-size limit, security settings and remaining single-process limitations.
- [ ] Run compileall, pip check, full pytest, JavaScript syntax, Compose config, Host attack reproduction, production fail-closed reproduction and `git diff --check`.
