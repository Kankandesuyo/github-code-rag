from pathlib import Path
import time

from app.config import get_settings
from app.schemas.report_schema import AgentLog, RepositoryAnalysis
from app.services.file_parser import get_language
from app.utils.file_utils import should_ignore_dir, should_ignore_file


ENTRY_POINT_NAMES = {
    "main.py",
    "app.py",
    "manage.py",
    "server.py",
    "run.py",
    "wsgi.py",
    "asgi.py",
    "server.js",
    "server.ts",
    "index.js",
    "index.ts",
    "app.js",
    "app.ts",
    "main.go",
    "main.rs",
    "Program.cs",
    "Dockerfile",
}


class RepositoryAgent:
    name = "RepositoryAgent"

    def resolve_repo_path(self, repository_id: str) -> Path:
        repo_path = get_settings().repos_dir / repository_id
        if not repo_path.exists() or not repo_path.is_dir():
            raise FileNotFoundError(f"repository not found: {repository_id}")
        snapshot_path = repo_path / "source_snapshot"
        if snapshot_path.exists() and snapshot_path.is_dir():
            return snapshot_path
        return repo_path

    def iter_files(self, repo_path: Path) -> list[Path]:
        settings = get_settings()
        files: list[Path] = []
        total_bytes = 0
        for path in repo_path.rglob("*"):
            relative = path.relative_to(repo_path)
            if any(should_ignore_dir(parent) for parent in relative.parents):
                continue
            if path.is_symlink():
                continue
            if path.is_dir():
                continue
            if should_ignore_file(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > settings.max_file_size_bytes:
                continue
            if len(files) >= settings.max_repository_files:
                break
            if total_bytes + size > settings.max_repository_bytes:
                continue
            files.append(path)
            total_bytes += size
        return files

    def count_directories(self, repo_path: Path) -> int:
        count = 0
        for path in repo_path.rglob("*"):
            if not path.is_dir():
                continue
            if path.is_symlink():
                continue
            relative = path.relative_to(repo_path)
            if any(should_ignore_dir(parent) for parent in relative.parents) or should_ignore_dir(path):
                continue
            count += 1
        return count

    def build_directory_tree(self, repo_path: Path, max_depth: int = 3, max_entries: int = 220) -> str:
        lines = [repo_path.name + "/"]
        entries_added = 0

        def add_children(directory: Path, prefix: str, depth: int) -> None:
            nonlocal entries_added
            if depth > max_depth or entries_added >= max_entries:
                return
            children = [
                child
                for child in sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
                if not child.is_symlink() and not should_ignore_dir(child) and not (child.is_file() and should_ignore_file(child))
            ]
            for index, child in enumerate(children):
                if entries_added >= max_entries:
                    break
                connector = "`-- " if index == len(children) - 1 else "|-- "
                suffix = "/" if child.is_dir() else ""
                lines.append(f"{prefix}{connector}{child.name}{suffix}")
                entries_added += 1
                if child.is_dir():
                    extension = "    " if index == len(children) - 1 else "|   "
                    add_children(child, prefix + extension, depth + 1)

        add_children(repo_path, "", 1)
        if entries_added >= max_entries:
            lines.append("... truncated ...")
        return "\n".join(lines)

    def detect_entry_points(self, repo_path: Path, files: list[Path]) -> list[str]:
        entry_points: list[str] = []
        for path in files:
            relative = path.relative_to(repo_path).as_posix()
            if path.name in ENTRY_POINT_NAMES:
                entry_points.append(relative)
                continue
            if relative in {"src/main.py", "src/index.ts", "src/index.js", "app/main.py"}:
                entry_points.append(relative)
        return sorted(set(entry_points))

    def detect_core_modules(self, repo_path: Path, files: list[Path]) -> list[dict]:
        module_stats: dict[str, dict] = {}
        for path in files:
            relative = path.relative_to(repo_path)
            parts = relative.parts
            module = parts[0] if len(parts) > 1 else "."
            if module.startswith("."):
                module = "."
            stats = module_stats.setdefault(
                module,
                {"path": module, "file_count": 0, "languages": set(), "size_bytes": 0},
            )
            stats["file_count"] += 1
            stats["size_bytes"] += path.stat().st_size
            stats["languages"].add(get_language(path.name))

        modules = []
        for stats in module_stats.values():
            modules.append(
                {
                    "path": stats["path"],
                    "file_count": stats["file_count"],
                    "languages": sorted(stats["languages"]),
                    "size_bytes": stats["size_bytes"],
                }
            )
        modules.sort(key=lambda item: (item["file_count"], item["size_bytes"]), reverse=True)
        return modules[:12]

    def analyze(self, repository_id: str) -> tuple[RepositoryAnalysis, list[AgentLog]]:
        started = time.perf_counter()
        repo_path = self.resolve_repo_path(repository_id)
        files = self.iter_files(repo_path)
        languages = sorted({get_language(path.name) for path in files})
        largest_files = sorted(
            [
                {
                    "file_path": path.relative_to(repo_path).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "language": get_language(path.name),
                }
                for path in files
            ],
            key=lambda item: item["size_bytes"],
            reverse=True,
        )[:10]

        analysis = RepositoryAnalysis(
            file_count=len(files),
            directory_count=self.count_directories(repo_path),
            languages=languages,
            largest_files=largest_files,
            entry_points=self.detect_entry_points(repo_path, files),
            directory_tree=self.build_directory_tree(repo_path),
            core_modules=self.detect_core_modules(repo_path, files),
        )
        logs = [
            AgentLog(
                agent=self.name,
                action="Scanning repository",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
        return analysis, logs
