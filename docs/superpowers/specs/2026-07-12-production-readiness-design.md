# GitHub Code RAG Production Readiness Design

## Goal

Turn the current local MVP into a coherent, testable delivery: remote GitHub imports must support both RAG and Agent artifacts, repository state must be committed safely, the main user journey must have automated acceptance coverage, Chroma must shut down cleanly without telemetry errors, and deployment must include Docker, CI, and single-administrator authentication.

## Milestone 1: Remote Analysis Snapshot

The GitHub loader already downloads filtered text in memory and sends chunks to Chroma. It will additionally create a controlled analysis snapshot under `repos/<repository_id>/source_snapshot/`. Only files that have already passed the existing path, size, binary, and sensitive-file filters may be written. Paths are resolved and checked against the snapshot root before writing.

`RepositoryAgent` and the analyzers will transparently use this snapshot as their source root. Runtime manifests remain under `.codebase_agent/`. Re-import replaces the snapshot atomically enough for a single-process MVP: write a staging directory, remove the old snapshot, then rename staging into place. A failed write must not leave a partially valid snapshot.

## Milestone 2: Git Baseline

All source, test, documentation, UI, and delivery assets that belong to the product will be tracked. Runtime secrets, virtual environments, logs, downloaded repository data, vector data, caches, and generated distribution folders remain ignored. The migration from `app/schemas.py` to the `app/schemas/` package is committed as one coherent change.

## Milestone 3: End-to-End Acceptance

An automated FastAPI acceptance test will exercise the main journey with deterministic local substitutes for external GitHub, embedding, and LLM services:

1. import a representative remote file set;
2. create the analysis snapshot and vector index;
3. ask a question and receive grounded sources;
4. generate a project report containing detected technology and API evidence;
5. generate a README containing repository evidence.

External provider calls remain covered by focused adapter tests and a documented optional live smoke test. CI must not depend on GitHub rate limits, model downloads, or paid API keys.

## Milestone 4: Chroma Lifecycle

Telemetry failures are caused by the Chroma 0.5.x integration calling an incompatible modern PostHog API even when anonymized telemetry is disabled. Dependency constraints will select a compatible PostHog release. The cached Chroma client will gain an explicit shutdown function that clears the cache, invokes supported client/system shutdown hooks, and is called during FastAPI shutdown. Tests will verify cache release and ensure telemetry errors are absent from the acceptance command.

## Milestone 5: Deployment and Authentication

The deployment target is a single administrator, not a multi-tenant SaaS. Authentication uses:

- `ADMIN_USERNAME` and a password hash configured only on the server;
- signed, expiring server-side-verifiable session cookies;
- `HttpOnly`, `SameSite=Strict`, and production `Secure` cookie flags;
- login rate limiting;
- CSRF tokens for state-changing authenticated requests;
- `/auth/status`, `/auth/login`, and `/auth/logout` endpoints;
- business routes protected when administrator auth is configured;
- a same-origin login panel in the existing frontend.

The legacy `APP_API_KEY` remains available for programmatic clients. If administrator credentials are configured, browser sessions or a valid API key may authorize business endpoints. Health checks and static login assets remain public.

Docker uses a non-root runtime user, persistent volumes for `repos/` and `chroma_db/`, and a health check. GitHub Actions installs pinned dependencies and runs compile, dependency, and test checks.

## Security Boundaries

- Never persist `.env`, private keys, tokens, credential files, binaries, symlinks, or oversized files in snapshots.
- Never store the administrator plaintext password in the repository or logs.
- Never expose the API key or administrator password through frontend source.
- Reject unsafe repository IDs and snapshot traversal paths.
- Use constant-time comparisons for secrets.
- Do not claim multi-user ownership isolation; this design is deliberately single-admin.

## Documentation and Acceptance

Each milestone is recorded in `UPDATE_README.md`; security-relevant behavior is also recorded in `SECURITY_LOG.md`. Completion requires a clean Git worktree, full pytest pass, compileall pass, `pip check`, a real local Uvicorn health check, and successful Docker configuration validation when Docker is available.
