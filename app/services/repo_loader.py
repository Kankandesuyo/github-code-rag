import base64
import binascii
import hashlib
import html
import json
import re
import shutil
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from pathlib import PurePosixPath
from typing import Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

from app.config import get_settings
from app.services.file_parser import ParsedFile, split_files_into_chunks
from app.utils.file_utils import should_ignore_dir, should_ignore_file


class RepositoryLoadError(RuntimeError):
    pass


@dataclass
class ImportBudget:
    max_requests: int
    max_directories: int
    timeout_seconds: float
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    outbound_requests: int = 0
    visited_directories: int = 0
    _deadline: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_requests < 1 or self.max_directories < 1 or self.timeout_seconds <= 0:
            raise ValueError("repository import budget limits must be positive")
        self._deadline = self.clock() + self.timeout_seconds

    @classmethod
    def from_settings(cls) -> "ImportBudget":
        settings = get_settings()
        return cls(
            max_requests=settings.max_repository_requests,
            max_directories=settings.max_repository_directories,
            timeout_seconds=settings.repository_import_timeout_seconds,
        )

    def check_deadline(self) -> None:
        self.remaining_seconds()

    def remaining_seconds(self) -> float:
        remaining = self._deadline - self.clock()
        if remaining <= 0:
            raise RepositoryLoadError("repository import deadline exceeded")
        return remaining

    def effective_timeout(self, configured_timeout_seconds: float) -> float:
        return min(float(configured_timeout_seconds), self.remaining_seconds())

    def record_request(self) -> None:
        self.check_deadline()
        if self.outbound_requests >= self.max_requests:
            raise RepositoryLoadError("repository import request limit exceeded")
        self.outbound_requests += 1

    def record_directory(self) -> None:
        self.check_deadline()
        if self.visited_directories >= self.max_directories:
            raise RepositoryLoadError("repository import directory limit exceeded")
        self.visited_directories += 1


_ACTIVE_IMPORT_BUDGET: ContextVar[ImportBudget | None] = ContextVar(
    "active_repository_import_budget",
    default=None,
)


def _resolve_import_budget(budget: ImportBudget | None = None) -> ImportBudget | None:
    return budget if budget is not None else _ACTIVE_IMPORT_BUDGET.get()


@contextmanager
def activate_import_budget(budget: ImportBudget) -> Iterator[None]:
    if _ACTIVE_IMPORT_BUDGET.get() is budget:
        yield
        return
    token = _ACTIVE_IMPORT_BUDGET.set(budget)
    try:
        yield
    finally:
        _ACTIVE_IMPORT_BUDGET.reset(token)


GITHUB_OWNER_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
REMOTE_REPOSITORY_MANIFEST_FILE = "remote_repository_manifest.json"
REMOTE_ANALYSIS_SNAPSHOT_DIR = "source_snapshot"
GITHUB_WEB_RATE_LIMIT_MARKER = "set server-side GITHUB_TOKEN"


def generate_repository_id(github_url: str) -> str:
    normalized = github_url.rstrip("/")
    digest = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:10]
    parsed = urlparse(normalized)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    readable = "-".join(parts[-2:]) if len(parts) >= 2 else "repository"
    readable = re.sub(r"[^a-zA-Z0-9_-]+", "-", readable).strip("-")
    return f"{readable}-{digest}"


def validate_github_repo_url(github_url: str) -> str:
    parsed = urlparse(github_url.strip())
    if parsed.scheme != "https":
        raise RepositoryLoadError("only https:// GitHub repository URLs are allowed")
    if parsed.netloc.lower() != "github.com":
        raise RepositoryLoadError("only github.com repository URLs are allowed")
    if parsed.username or parsed.password:
        raise RepositoryLoadError("credentials in GitHub URLs are not allowed")
    if parsed.params or parsed.query or parsed.fragment:
        raise RepositoryLoadError("GitHub repository URL must not include params, query, or fragment")

    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise RepositoryLoadError("GitHub repository URL must be https://github.com/{owner}/{repo}")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise RepositoryLoadError("GitHub repository URL must include owner and repository name")
    if not GITHUB_OWNER_REPO_PATTERN.fullmatch(owner) or not GITHUB_OWNER_REPO_PATTERN.fullmatch(repo):
        raise RepositoryLoadError("GitHub owner or repository name contains invalid characters")
    if owner in {".", ".."} or repo in {".", ".."}:
        raise RepositoryLoadError("GitHub owner or repository name is invalid")

    return f"https://github.com/{owner}/{repo}.git"


def parse_github_owner_repo(github_url: str) -> tuple[str, str]:
    parsed = urlparse(github_url)
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise RepositoryLoadError("GitHub repository URL must be https://github.com/{owner}/{repo}")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def build_github_headers(accept: str = "application/vnd.github+json") -> dict[str, str]:
    settings = get_settings()
    headers = {
        "User-Agent": "github-code-rag",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = settings.github_token.strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_github_json(
    url: str,
    timeout_seconds: int,
    budget: ImportBudget | None = None,
) -> dict:
    request = Request(url, headers=build_github_headers())
    try:
        active_budget = _resolve_import_budget(budget)
        if active_budget is not None:
            active_budget.record_request()
        request_timeout = (
            active_budget.effective_timeout(timeout_seconds)
            if active_budget is not None
            else timeout_seconds
        )
        with urlopen(request, timeout=request_timeout) as response:
            data = response.read()
            if active_budget is not None:
                active_budget.check_deadline()
            return json.loads(data.decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise RepositoryLoadError("GitHub repository or file was not found") from exc
        if exc.code == 403:
            raise RepositoryLoadError(f"GitHub API rate limit or permission blocked the request; {GITHUB_WEB_RATE_LIMIT_MARKER} to raise the limit") from exc
        raise RepositoryLoadError(f"GitHub API request failed with status {exc.code}") from exc
    except URLError as exc:
        raise RepositoryLoadError(f"GitHub API network error: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RepositoryLoadError("GitHub API returned invalid JSON") from exc


def fetch_url_bytes(
    url: str,
    timeout_seconds: int,
    accept: str,
    max_bytes: int | None = None,
    budget: ImportBudget | None = None,
) -> bytes:
    request = Request(url, headers=build_github_headers(accept=accept))
    try:
        active_budget = _resolve_import_budget(budget)
        if active_budget is not None:
            active_budget.record_request()
        request_timeout = (
            active_budget.effective_timeout(timeout_seconds)
            if active_budget is not None
            else timeout_seconds
        )
        with urlopen(request, timeout=request_timeout) as response:
            content_length = response.headers.get("Content-Length")
            if max_bytes is not None and content_length and int(content_length) > max_bytes:
                raise RepositoryLoadError("remote GitHub file is too large")
            if max_bytes is None:
                data = response.read()
            else:
                data = response.read(max_bytes + 1)
            if active_budget is not None:
                active_budget.check_deadline()
            if max_bytes is not None and len(data) > max_bytes:
                raise RepositoryLoadError("remote GitHub file exceeded size limit")
            return data
    except HTTPError as exc:
        if exc.code == 404:
            raise RepositoryLoadError("GitHub web page or raw file was not found") from exc
        if exc.code == 403:
            raise RepositoryLoadError("GitHub web page access was blocked; set server-side GITHUB_TOKEN or try again later") from exc
        raise RepositoryLoadError(f"GitHub web request failed with status {exc.code}") from exc
    except URLError as exc:
        raise RepositoryLoadError(f"GitHub web network error: {exc.reason}") from exc


def fetch_url_text(
    url: str,
    timeout_seconds: int,
    accept: str = "text/html",
    budget: ImportBudget | None = None,
) -> str:
    data = fetch_url_bytes(url, timeout_seconds, accept, budget=budget)
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise RepositoryLoadError(f"failed to decode GitHub web page: {url}")


def get_github_default_branch(owner: str, repo: str, timeout_seconds: int) -> str:
    payload = fetch_github_json(f"https://api.github.com/repos/{owner}/{repo}", timeout_seconds)
    default_branch = payload.get("default_branch")
    if not isinstance(default_branch, str) or not default_branch:
        raise RepositoryLoadError("GitHub API did not return a default branch")
    return default_branch


def get_github_tree(owner: str, repo: str, branch: str, timeout_seconds: int) -> list[dict]:
    encoded_branch = quote(branch, safe="")
    payload = fetch_github_json(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{encoded_branch}?recursive=1",
        timeout_seconds,
    )
    if payload.get("truncated"):
        raise RepositoryLoadError("GitHub repository tree is too large for browser traversal")
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise RepositoryLoadError("GitHub API did not return a repository tree")
    return tree


def should_skip_remote_path(file_path: str) -> bool:
    path = Path(file_path)
    if any(should_ignore_dir(parent) for parent in path.parents):
        return True
    return should_ignore_file(path)


def should_skip_remote_dir(dir_path: str) -> bool:
    path = Path(dir_path)
    return should_ignore_dir(path) or any(should_ignore_dir(parent) for parent in path.parents)


def decode_github_blob(payload: dict, file_path: str) -> str | None:
    encoding = payload.get("encoding")
    content = payload.get("content")
    if encoding != "base64" or not isinstance(content, str):
        return None
    try:
        raw = base64.b64decode(content, validate=False)
    except (binascii.Error, ValueError):
        return None
    if b"\x00" in raw:
        return None
    for text_encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936", "cp1252", "latin-1"):
        try:
            return raw.decode(text_encoding)
        except UnicodeDecodeError:
            continue
    raise RepositoryLoadError(f"failed to decode remote file: {file_path}")


def fetch_remote_file(entry: dict, timeout_seconds: int) -> ParsedFile | None:
    file_path = entry.get("path")
    blob_url = entry.get("url")
    size = entry.get("size", 0)
    settings = get_settings()

    if not isinstance(file_path, str) or not isinstance(blob_url, str):
        return None
    if not isinstance(size, int) or size <= 0 or size > settings.max_file_size_bytes:
        return None
    if should_skip_remote_path(file_path):
        return None

    payload = fetch_github_json(blob_url, timeout_seconds)
    content = decode_github_blob(payload, file_path)
    if content is None or not content.strip():
        return None
    return ParsedFile(file_path=file_path, content=content)


def is_api_rate_limit_error(exc: RepositoryLoadError) -> bool:
    return GITHUB_WEB_RATE_LIMIT_MARKER in str(exc)


def parse_default_branch_from_web_page(page: str, owner: str, repo: str) -> str:
    embedded_data = parse_web_embedded_data(page)
    ref_info = (
        embedded_data.get("payload", {})
        .get("codeViewRepoRoute", {})
        .get("refInfo", {})
    )
    branch_name = ref_info.get("name")
    if isinstance(branch_name, str) and branch_name:
        return branch_name

    escaped_owner = re.escape(owner)
    escaped_repo = re.escape(repo)
    patterns = [
        rf'href="/{escaped_owner}/{escaped_repo}/tree/([^"/?#]+)',
        rf'href="/{escaped_owner}/{escaped_repo}/blob/([^"/?#]+)',
    ]
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(html.unescape(match) for match in re.findall(pattern, page))
    for preferred in ("main", "master"):
        if preferred in candidates:
            return preferred
    if candidates:
        return candidates[0]
    raise RepositoryLoadError("could not detect the GitHub default branch from the browser page")


def parse_web_embedded_data(page: str) -> dict:
    match = re.search(
        r'<script[^>]+data-target="react-app\.embeddedData"[^>]*>(.*?)</script>',
        page,
        flags=re.DOTALL,
    )
    if not match:
        return {}
    try:
        payload = html.unescape(match.group(1)).strip()
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_web_directory_entries(page: str, owner: str, repo: str, branch: str) -> list[tuple[str, str]]:
    embedded_data = parse_web_embedded_data(page)
    items = (
        embedded_data.get("payload", {})
        .get("codeViewRepoRoute", {})
        .get("tree", {})
        .get("items", [])
    )
    escaped_owner = re.escape(owner)
    escaped_repo = re.escape(repo)
    escaped_branch = re.escape(branch)
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            file_path = item.get("path")
            content_type = item.get("contentType")
            if not isinstance(file_path, str) or not isinstance(content_type, str):
                continue
            kind = "tree" if content_type == "directory" else "blob" if content_type == "file" else ""
            if not kind:
                continue
            entry = (kind, file_path.strip("/"))
            if entry in seen:
                continue
            seen.add(entry)
            entries.append(entry)
        if entries:
            return entries

    pattern = rf'href="(/{escaped_owner}/{escaped_repo}/(blob|tree)/{escaped_branch}/[^"#?]+)"'
    for href, kind in re.findall(pattern, page):
        marker = f"/{owner}/{repo}/{kind}/{branch}/"
        if marker not in href:
            continue
        file_path = unquote(html.unescape(href.split(marker, 1)[1])).strip("/")
        if not file_path:
            continue
        item = (kind, file_path)
        if item in seen:
            continue
        seen.add(item)
        entries.append(item)
    return entries


def decode_raw_file(data: bytes, file_path: str) -> str | None:
    if b"\x00" in data:
        return None
    for text_encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936", "cp1252", "latin-1"):
        try:
            return data.decode(text_encoding)
        except UnicodeDecodeError:
            continue
    raise RepositoryLoadError(f"failed to decode remote file: {file_path}")


def fetch_web_raw_file(owner: str, repo: str, branch: str, file_path: str, timeout_seconds: int) -> ParsedFile | None:
    settings = get_settings()
    if should_skip_remote_path(file_path):
        return None
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{quote(branch, safe='')}/{quote(file_path, safe='/')}"
    data = fetch_url_bytes(
        raw_url,
        timeout_seconds,
        accept="text/plain,application/octet-stream",
        max_bytes=settings.max_file_size_bytes,
    )
    content = decode_raw_file(data, file_path)
    if content is None or not content.strip():
        return None
    return ParsedFile(file_path=file_path, content=content)


def browse_github_repository_via_web(
    owner: str,
    repo: str,
    repository_id: str,
    budget: ImportBudget | None = None,
) -> list[ParsedFile]:
    active_budget = _resolve_import_budget(budget) or ImportBudget.from_settings()
    with activate_import_budget(active_budget):
        settings = get_settings()
        timeout = settings.github_api_timeout_seconds
        default_branch: str | None = None
        files: list[ParsedFile] = []
        total_bytes = 0
        visited_dirs: set[str] = set()
        pending_dirs = [""]

        while pending_dirs and len(files) < settings.max_repository_files:
            directory_path = pending_dirs.pop()
            if directory_path in visited_dirs:
                continue
            active_budget.record_directory()
            visited_dirs.add(directory_path)

            if directory_path:
                if default_branch is None:
                    raise RepositoryLoadError("could not detect the GitHub default branch from the browser page")
                page_url = (
                    f"https://github.com/{owner}/{repo}/tree/"
                    f"{quote(default_branch, safe='')}/{quote(directory_path, safe='/')}"
                )
            else:
                page_url = f"https://github.com/{owner}/{repo}"
            page = fetch_url_text(page_url, timeout)
            if default_branch is None:
                default_branch = parse_default_branch_from_web_page(page, owner, repo)

            for kind, file_path in parse_web_directory_entries(page, owner, repo, default_branch):
                if kind == "tree":
                    if file_path in visited_dirs or should_skip_remote_dir(file_path):
                        continue
                    pending_dirs.append(file_path)
                    continue

                if len(files) >= settings.max_repository_files:
                    break
                if should_skip_remote_path(file_path):
                    continue
                parsed_file = fetch_web_raw_file(owner, repo, default_branch, file_path, timeout)
                if parsed_file is None:
                    continue
                file_bytes = len(parsed_file.content.encode("utf-8", errors="ignore"))
                if total_bytes + file_bytes > settings.max_repository_bytes:
                    continue
                files.append(parsed_file)
                total_bytes += file_bytes

        if not files:
            raise RepositoryLoadError("no supported source files were found by GitHub browser traversal")

        save_remote_repository_manifest(
            repository_id,
            f"https://github.com/{owner}/{repo}.git",
            default_branch,
            files,
            total_bytes,
            "github_web_browser",
        )
        return files


def browse_github_repository(
    github_url: str,
    repository_id: str,
    budget: ImportBudget | None = None,
) -> list[ParsedFile]:
    active_budget = _resolve_import_budget(budget) or ImportBudget.from_settings()
    with activate_import_budget(active_budget):
        settings = get_settings()
        owner, repo = parse_github_owner_repo(github_url)
        try:
            default_branch = get_github_default_branch(owner, repo, settings.github_api_timeout_seconds)
            tree = get_github_tree(owner, repo, default_branch, settings.github_api_timeout_seconds)
        except RepositoryLoadError as exc:
            if is_api_rate_limit_error(exc):
                return browse_github_repository_via_web(owner, repo, repository_id)
            raise

        files: list[ParsedFile] = []
        total_bytes = 0
        active_budget.record_directory()
        for entry in sorted(tree, key=lambda item: str(item.get("path", ""))):
            active_budget.check_deadline()
            if entry.get("type") == "tree":
                directory_path = entry.get("path", "")
                if isinstance(directory_path, str) and not should_skip_remote_dir(directory_path):
                    active_budget.record_directory()
                continue
            if entry.get("type") != "blob":
                continue
            size = entry.get("size", 0)
            if not isinstance(size, int):
                continue
            file_path = entry.get("path", "")
            if not isinstance(file_path, str) or should_skip_remote_path(file_path):
                continue
            if len(files) >= settings.max_repository_files:
                break
            if total_bytes + size > settings.max_repository_bytes:
                continue
            parsed_file = fetch_remote_file(entry, settings.github_api_timeout_seconds)
            if parsed_file is None:
                continue
            files.append(parsed_file)
            total_bytes += size

        if not files:
            raise RepositoryLoadError("no supported source files were found by browser traversal")

        save_remote_repository_manifest(
            repository_id,
            github_url,
            default_branch,
            files,
            total_bytes,
            "github_api_browser",
        )
        return files


def save_remote_repository_manifest(
    repository_id: str,
    github_url: str,
    default_branch: str,
    files: list[ParsedFile],
    total_bytes: int,
    source: str,
) -> None:
    settings = get_settings()
    manifest_dir = settings.repos_dir / repository_id / ".codebase_agent"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source": source,
        "github_url": github_url,
        "default_branch": default_branch,
        "files_indexed": len(files),
        "total_remote_bytes": total_bytes,
        "file_paths": [item.file_path for item in files],
    }
    (manifest_dir / REMOTE_REPOSITORY_MANIFEST_FILE).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_remote_analysis_snapshot(repository_id: str, files: list[ParsedFile]) -> Path:
    settings = get_settings()
    repository_dir = settings.repos_dir / repository_id
    repository_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = repository_dir / REMOTE_ANALYSIS_SNAPSHOT_DIR
    staging_dir = repository_dir / f".{REMOTE_ANALYSIS_SNAPSHOT_DIR}.staging"

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    try:
        for item in files:
            normalized = item.file_path.replace("\\", "/").strip("/")
            relative = PurePosixPath(normalized)
            if not normalized or relative.is_absolute() or ".." in relative.parts:
                continue
            path = Path(*relative.parts)
            if should_skip_remote_path(path.as_posix()):
                continue
            destination = (staging_dir / path).resolve()
            try:
                destination.relative_to(staging_dir.resolve())
            except ValueError:
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(item.content, encoding="utf-8")

        if snapshot_dir.exists():
            shutil.rmtree(snapshot_dir)
        staging_dir.replace(snapshot_dir)
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise RepositoryLoadError(f"failed to save remote analysis snapshot: {exc}") from exc

    return snapshot_dir


def load_repository(github_url: str, budget: ImportBudget | None = None):
    active_budget = _resolve_import_budget(budget) or ImportBudget.from_settings()
    with activate_import_budget(active_budget):
        active_budget.check_deadline()
        normalized_url = validate_github_repo_url(github_url)
        repository_id = generate_repository_id(normalized_url)
        files = browse_github_repository(normalized_url, repository_id)
        active_budget.check_deadline()
        save_remote_analysis_snapshot(repository_id, files)
        active_budget.check_deadline()
        chunks = split_files_into_chunks(files, repository_id)
        active_budget.check_deadline()
        return repository_id, chunks, len(files)
