import re
from pathlib import Path

from app.schemas.report_schema import EntrypointFinding


STARTUP_ENTRY_NAMES = {"main.py", "app.py", "manage.py", "server.js", "server.ts", "index.js", "index.ts"}
CONFIG_ENTRY_NAMES = {
    ".env",
    ".env.example",
    "settings.py",
    "config.py",
    "config.js",
    "config.ts",
    "pyproject.toml",
    "package.json",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
}


class EntrypointAnalyzer:
    def __init__(self, files: list[Path]) -> None:
        self.files = files

    def read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def classify_file(self, repo_path: Path, path: Path) -> list[EntrypointFinding]:
        findings: list[EntrypointFinding] = []
        relative = path.relative_to(repo_path).as_posix()
        name = path.name
        content = self.read_text_file(path)

        if name in STARTUP_ENTRY_NAMES or relative in {"app/main.py", "src/index.js", "src/index.ts"}:
            findings.append(EntrypointFinding(kind="startup", file_path=relative, reason=f"recognized entry filename `{name}`"))

        if path.suffix == ".py" and re.search(r"\b(FastAPI|Flask)\s*\(", content):
            findings.append(EntrypointFinding(kind="application", file_path=relative, reason="creates a Python web application object"))
        if path.suffix.lower() in {".js", ".ts"} and re.search(r"\b(express\(\)|createServer|app\.listen)\b", content):
            findings.append(EntrypointFinding(kind="application", file_path=relative, reason="creates or starts a Node web application"))

        if name in CONFIG_ENTRY_NAMES or relative in CONFIG_ENTRY_NAMES:
            findings.append(EntrypointFinding(kind="configuration", file_path=relative, reason=f"recognized configuration file `{name}`"))

        return findings

    def analyze(self, repo_path: Path) -> list[EntrypointFinding]:
        findings: list[EntrypointFinding] = []
        seen: set[tuple[str, str]] = set()
        for path in self.files:
            for finding in self.classify_file(repo_path, path):
                key = (finding.kind, finding.file_path)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
        return findings[:80]
