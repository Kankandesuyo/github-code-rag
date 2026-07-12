# GitHub Code RAG Production Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the five approved production-readiness milestones and leave a tested, documented, committed repository.

**Architecture:** Reuse the already-filtered remote file list as a safe local analysis snapshot, then make Agent analyzers consume that snapshot. Add deterministic acceptance coverage, explicit Chroma lifecycle management, and an opt-in single-admin signed-cookie authentication layer while preserving API-key automation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic Settings, ChromaDB, pytest, vanilla HTML/CSS/JS, Docker, GitHub Actions.

## Global Constraints

- Preserve the product rule that a remote import does not clone Git history or an entire repository archive.
- Persist only text files that already passed the existing sensitive-path, binary, size, file-count, and total-byte filters.
- Keep local development usable when administrator credentials are not configured.
- Use tests first for every behavior change and record each milestone in the project report.
- Do not expose secrets in code, logs, frontend assets, tests, or documentation examples.

---

### Task 1: Safe remote analysis snapshot

**Files:**
- Modify: `app/services/repo_loader.py`
- Modify: `app/agents/repository_agent.py`
- Test: `tests/test_v2_agents.py`

**Interfaces:**
- Produces: `save_remote_analysis_snapshot(repository_id: str, files: list[ParsedFile]) -> Path`
- Produces: `RepositoryAgent.resolve_source_path(repository_id: str) -> Path`

- [ ] Add a failing test that imports remote `ParsedFile` objects, asserts safe files appear under `source_snapshot/`, asserts sensitive/traversal paths do not, and asserts the generated report detects FastAPI.
- [ ] Run the focused test and confirm it fails because the snapshot API is absent.
- [ ] Implement staging-directory snapshot writes with root containment checks and cleanup on failure.
- [ ] Make `RepositoryAgent` resolve `source_snapshot/` when present.
- [ ] Run the focused test and the full suite.
- [ ] Append verified behavior to `UPDATE_README.md` and `SECURITY_LOG.md`.

### Task 2: Coherent Git baseline

**Files:**
- Modify: `.gitignore`
- Track: application, tests, docs, static assets, and approved screenshots

**Interfaces:**
- Produces: a repository where source code required by imports is tracked and runtime data remains ignored.

- [ ] Audit every untracked path and confirm no `.env`, tokens, runtime repositories, vector databases, logs, or caches will be staged.
- [ ] Verify `git check-ignore` for secret/runtime paths.
- [ ] Stage the coherent application baseline and inspect `git diff --cached --stat` plus `git diff --cached --check`.
- [ ] Commit the baseline after the full test suite passes.

### Task 3: Main-journey acceptance test

**Files:**
- Create: `tests/test_end_to_end.py`
- Modify: application seams only if the test reveals missing dependency injection.

**Interfaces:**
- Consumes: FastAPI `app`, remote loader, Chroma index, report service, and README endpoint.
- Produces: one deterministic test for import, chat, report, and README behavior.

- [ ] Write a failing TestClient test with a representative FastAPI repository and deterministic hash embeddings/LLM fallback.
- [ ] Run it and confirm the failure identifies the first missing cross-component behavior.
- [ ] Apply only the minimal seam or implementation fix required.
- [ ] Run the focused acceptance test and then all tests.
- [ ] Document the covered journey and external-live-test boundary in `UPDATE_README.md`.

### Task 4: Chroma telemetry and lifecycle

**Files:**
- Modify: `requirements.txt`
- Modify: `app/services/vector_store.py`
- Modify: `app/main.py`
- Test: `tests/test_v2_agents.py`

**Interfaces:**
- Produces: `close_chroma_client() -> None`
- Consumes: FastAPI lifespan/shutdown event.

- [ ] Add failing tests that create a cached fake client and assert shutdown clears/recreates it, and that application shutdown invokes the closer.
- [ ] Reproduce and record the PostHog signature mismatch with installed versions.
- [ ] Pin a Chroma-compatible PostHog range and implement explicit client/system shutdown with cache clearing.
- [ ] Run focused tests, reinstall/check dependencies, then run a temporary-directory Chroma smoke test and verify clean process exit.
- [ ] Record root cause and resolution in both reports.

### Task 5: Single-administrator authentication

**Files:**
- Create: `app/security/auth.py`
- Create: `app/security/__init__.py`
- Modify: `app/config.py`
- Modify: `app/main.py`
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `.env.example`
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces: `/auth/status`, `/auth/login`, `/auth/logout`
- Produces: signed cookie session and CSRF validation dependencies.
- Preserves: `X-API-Key` authorization for programmatic clients.

- [ ] Write failing tests for disabled-auth local mode, valid/invalid login, signed-cookie tampering, expiry, logout, CSRF rejection, API-key compatibility, and login rate limiting.
- [ ] Run tests and confirm missing auth behavior failures.
- [ ] Implement password-hash verification, HMAC-signed expiring sessions, CSRF tokens, and auth dependencies.
- [ ] Protect business routes while keeping `/`, static assets, health, and auth endpoints public.
- [ ] Add the frontend login overlay and same-origin credential/CSRF handling without storing secrets in localStorage.
- [ ] Run auth tests, frontend smoke checks, and the full suite.
- [ ] Update README configuration/run instructions and both project/security reports.

### Task 6: Docker and CI delivery

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `compose.yaml`
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`

**Interfaces:**
- Produces: container listening on port 8000 with persistent `/app/repos` and `/app/chroma_db`.
- Produces: CI checks for compileall, pip dependency integrity, and pytest.

- [ ] Add static delivery tests that assert non-root Docker execution, health check, required ignores, volume paths, and CI commands.
- [ ] Run them and confirm failure because delivery files are absent.
- [ ] Add minimal Docker, Compose, ignore, and CI files.
- [ ] Run static tests, `docker compose config` when Docker exists, and a container health smoke test when the daemon is available.
- [ ] Document exact local and Docker startup commands.

### Task 7: Final audit, report, and commits

**Files:**
- Modify: `UPDATE_README.md`
- Modify: `SECURITY_LOG.md`
- Modify: `README.md`

**Interfaces:**
- Produces: a clean, reproducible handoff with truthful verification evidence.

- [ ] Run `python -m compileall -q app tests`.
- [ ] Run `python -m pip check`.
- [ ] Run `python -m pytest tests -q`.
- [ ] Start Uvicorn on a temporary local port; verify health, homepage, auth status, and OpenAPI; stop it and confirm the port closes.
- [ ] Run `git diff --check`, inspect staged files for secret patterns, and verify `.env` remains untracked.
- [ ] Append exact fresh results to the reports.
- [ ] Commit the final verified state and confirm `git status --short --branch` is clean.
