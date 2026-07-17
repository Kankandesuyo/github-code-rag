# GitHub Code RAG Product Maturity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver a safe project catalog and deletion lifecycle, a beginner-friendly repository workspace, and verified local deployment while preserving the single-admin architecture and future tenant boundary.

**Architecture:** Add a focused `RepositoryCatalogService` between HTTP routes and runtime storage. It returns structured project summaries from existing manifests and coordinates root-contained filesystem and Chroma cleanup. The vanilla frontend consumes the structured API and presents a recoverable project workflow without changing the existing RAG, report, README, authentication, Docker, or CI contracts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, ChromaDB, pytest, vanilla HTML/CSS/JavaScript, Docker.

---

## File map

- Create `app/services/repository_catalog.py`: catalog summaries, manifest tolerance, root-contained deletion coordination.
- Modify `app/services/vector_store.py`: idempotent deletion of all collections belonging to one repository.
- Modify `app/main.py`: structured list/detail/delete endpoints and sanitized unexpected-error handling.
- Modify `app/static/index.html`: product onboarding, project summary, workspace controls, destructive-action confirmation.
- Modify `app/static/app.js`: structured catalog state, local active-project preference, error mapping, deletion and copy/download interactions.
- Modify `app/static/styles.css`: responsive product workspace and status presentation.
- Create `tests/test_repository_catalog.py`: unit and API behavior for catalog and deletion.
- Modify `tests/test_auth.py`: frontend security and workspace contract checks.
- Modify `README.md`, `UPDATE_README.md`, and `SECURITY_LOG.md`: product usage and security evidence.

### Task 1: Structured repository catalog

**Files:**
- Create: `app/services/repository_catalog.py`
- Modify: `app/main.py`
- Test: `tests/test_repository_catalog.py`

- [x] **Step 1: Write failing catalog tests**

Create tests that build temporary repository directories with `source_snapshot/` and `.codebase_agent/vector_index_manifest.json`, then assert `RepositoryCatalogService.list_repositories()` returns `repository_id`, `owner_id=None`, file/chunk counts, timestamps and status. Add a corrupt JSON fixture and assert it does not break the list.

- [x] **Step 2: Run the focused test and verify the missing service failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repository_catalog.py -q`

Expected: collection failure because `app.services.repository_catalog` does not exist.

- [x] **Step 3: Implement the catalog service**

Implement:

```python
@dataclass(frozen=True)
class RepositorySummary:
    repository_id: str
    owner_id: str | None
    status: str
    files_indexed: int
    chunks_indexed: int
    created_at: int | None
    updated_at: int | None

class RepositoryCatalogService:
    def list_repositories(self) -> list[RepositorySummary]: ...
    def get_repository(self, repository_id: str) -> RepositorySummary: ...
```

Only enumerate direct children of configured `repos_dir`. Validate identifiers with a service-local strict pattern, derive counts from the vector manifest, derive timestamps from manifest/filesystem metadata, log and tolerate corrupt optional JSON, and sort newest first.

- [x] **Step 4: Add backward-compatible API responses**

Change `GET /repositories` to return both the legacy string array and a new `items` array:

```json
{"repositories":["owner-repo-hash"],"items":[{"repository_id":"owner-repo-hash","owner_id":null,"status":"ready","files_indexed":12,"chunks_indexed":81,"created_at":1710000000,"updated_at":1710000030}]}
```

Add `GET /repositories/{repository_id}` with the same protected dependency set and a 404 for unknown projects.

- [x] **Step 5: Verify catalog behavior**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repository_catalog.py tests/test_auth.py -q`

Expected: all focused tests pass and existing `repositories` consumers remain compatible.

### Task 2: Safe project deletion lifecycle

**Files:**
- Modify: `app/services/repository_catalog.py`
- Modify: `app/services/vector_store.py`
- Modify: `app/main.py`
- Test: `tests/test_repository_catalog.py`

- [x] **Step 1: Write failing deletion tests**

Add tests for an existing project, an unknown project, an invalid identifier, a repository symlink, idempotent missing Chroma collections, and authenticated deletion without CSRF. Assert no sibling directory can be removed.

- [x] **Step 2: Run the focused tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repository_catalog.py -q`

Expected: failures because delete functions and the DELETE endpoint are absent.

- [x] **Step 3: Implement vector cleanup**

Add `delete_repository_collections(repository_id: str) -> int` to `vector_store.py`. Enumerate Chroma collections, select only collections whose metadata `repository_id` exactly matches, delete by collection name, and return the deletion count. Do not use prefix-only matching.

- [x] **Step 4: Implement root-contained filesystem cleanup**

Add `RepositoryCatalogService.delete_repository(repository_id)`. Reject invalid identifiers, resolve both root and candidate, require the candidate parent to be the resolved repository root, reject symlinks, return 404 semantics when absent, delete vector collections, then remove the controlled directory with `shutil.rmtree`.

- [x] **Step 5: Add protected DELETE endpoint**

Add `DELETE /repositories/{repository_id}` using the existing `require_api_key` dependency so browser sessions require a matching CSRF header while API-key clients remain compatible. Return `{"repository_id": "...", "deleted": true, "collections_deleted": 1}`.

- [x] **Step 6: Verify deletion and security regression**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_repository_catalog.py tests/test_auth.py tests/test_v2_agents.py -q`

Expected: all focused tests pass.

### Task 3: Beginner-friendly product workspace

**Files:**
- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Modify: `app/static/styles.css`
- Modify: `tests/test_auth.py`

- [x] **Step 1: Write failing frontend contract tests**

Assert the page contains a product value proposition, onboarding steps, project summary region, import progress region, delete confirmation dialog and mobile viewport. Assert JavaScript consumes `data.items`, remembers only the active repository ID, sends CSRF on deletion, maps 401/403/429 errors, and never writes credentials or tokens to browser storage.

- [x] **Step 2: Run frontend contract tests and verify failure**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_auth.py -q`

Expected: new workspace contract assertions fail.

- [x] **Step 3: Implement structured UI state**

Use one state object containing `repositories`, `activeRepositoryId`, `isLoading`, and `artifact`. Render structured project metadata, restore only `activeRepositoryId` from localStorage, and fall back to the newest available project.

- [x] **Step 4: Implement product workflow and error recovery**

Add a three-step onboarding area, a visible import stage, reusable `requestJson()` that maps authentication expiry, GitHub rate limiting, validation and server failures to Chinese user actions, and disabled/busy states for long-running operations.

- [x] **Step 5: Implement project actions**

Add safe delete confirmation using an HTML dialog, project refresh, report/README copy actions, Markdown file download, and an empty state that takes the user back to import. Deletion sends `X-CSRF-Token` and updates UI state only after a successful response.

- [x] **Step 6: Implement responsive visual hierarchy**

Preserve the existing visual language while separating navigation, project context and analysis workspace. At widths below 900px stack the sidebar and workspace; keep all buttons keyboard accessible, provide visible focus states and use live regions for loading/error status.

- [x] **Step 7: Verify frontend contracts and API journey**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_auth.py tests/test_end_to_end.py -q`

Expected: all tests pass.

### Task 4: Documentation, audit and real runtime verification

**Files:**
- Modify: `README.md`
- Modify: `UPDATE_README.md`
- Modify: `SECURITY_LOG.md`

- [x] **Step 1: Document the product workflow**

Document import, project reopening, grounded chat, report/README export and deletion. State explicitly that `owner_id` is a compatibility boundary and not current multi-tenant isolation.

- [x] **Step 2: Record security decisions**

Add a security-log entry covering root-contained deletion, exact metadata-based vector cleanup, CSRF enforcement, corrupt-manifest tolerance and the no-secret browser-storage rule.

- [x] **Step 3: Run complete static and automated checks**

Run:

```powershell
.\.venv\Scripts\python.exe -m compileall -q app tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest tests -q
git diff --check
```

Expected: compile exits 0, pip reports no broken requirements, pytest has zero failures, and git reports no whitespace errors.

- [x] **Step 4: Run a local Uvicorn smoke test**

Start `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8201`, then verify `/`, `/health`, `/auth/status`, `/repositories`, `/openapi.json`, and static JS/CSS. Stop the process and confirm port 8201 closes.

- [x] **Step 5: Inspect final change and secret boundary**

Run `git status --short`, `git diff --stat`, `git diff --check`, and `git check-ignore -v .env chroma_db repos`. Inspect the diff for passwords, tokens, absolute local paths, debug routes and stale claims.

- [x] **Step 6: Record fresh verification evidence**

Append exact test counts, endpoint status codes and any environment limitation such as an unavailable Docker daemon to `UPDATE_README.md`. Do not claim checks that were not run.

