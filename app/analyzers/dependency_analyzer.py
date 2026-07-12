import ast
import re
from pathlib import Path

from app.schemas.report_schema import ModuleDependency


PYTHON_SUFFIXES = {".py"}
JAVASCRIPT_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}


class DependencyAnalyzer:
    def __init__(self, files: list[Path]) -> None:
        self.files = files
        self.file_index: dict[str, str] = {}

    def read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def build_file_index(self, repo_path: Path) -> None:
        for path in self.files:
            relative = path.relative_to(repo_path).as_posix()
            self.file_index[relative] = relative
            if path.suffix == ".py":
                module = relative[:-3].replace("/", ".")
                self.file_index[module] = relative
                if relative.endswith("/__init__.py"):
                    package = relative[: -len("/__init__.py")].replace("/", ".")
                    self.file_index[package] = relative

    def resolve_python_module(self, module: str) -> str:
        if module in self.file_index:
            return self.file_index[module]
        parts = module.split(".")
        while parts:
            candidate = ".".join(parts)
            if candidate in self.file_index:
                return self.file_index[candidate]
            parts.pop()
        return module

    def resolve_relative_import(self, source_path: Path, target: str) -> str:
        if not target.startswith("."):
            return target
        base = source_path.parent
        raw = target
        while raw.startswith("."):
            if len(raw) > 1 and raw[1] == ".":
                base = base.parent
                raw = raw[1:]
            else:
                raw = raw[1:]
                break

        raw = raw.lstrip("/\\")
        if not raw:
            return target

        candidate = (base / raw).resolve()
        for suffix in JAVASCRIPT_SUFFIXES:
            path = candidate.with_suffix(suffix)
            if path.exists():
                return path.as_posix()
        for suffix in JAVASCRIPT_SUFFIXES:
            path = candidate / f"index{suffix}"
            if path.exists():
                return path.as_posix()
        return target

    def analyze_python_file(self, repo_path: Path, path: Path, relative: str) -> list[ModuleDependency]:
        content = self.read_text_file(path)
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        dependencies: list[ModuleDependency] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    dependencies.append(
                        ModuleDependency(
                            source_file=relative,
                            target=module,
                            resolved_target=self.resolve_python_module(module),
                            import_type="python import",
                            line=node.lineno,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                module = "." * node.level + node.module if node.level else node.module
                resolved = self.resolve_python_module(node.module)
                dependencies.append(
                    ModuleDependency(
                        source_file=relative,
                        target=module,
                        resolved_target=resolved,
                        import_type="python from-import",
                        line=node.lineno,
                    )
                )
        return dependencies

    def analyze_javascript_file(self, repo_path: Path, path: Path, relative: str) -> list[ModuleDependency]:
        content = self.read_text_file(path)
        dependencies: list[ModuleDependency] = []
        patterns = [
            (re.compile(r"\bimport\s+(?:[^\"'`]+?\s+from\s+)?[\"'`](?P<target>[^\"'`]+)[\"'`]"), "es import"),
            (re.compile(r"\brequire\(\s*[\"'`](?P<target>[^\"'`]+)[\"'`]\s*\)"), "commonjs require"),
            (re.compile(r"\bimport\(\s*[\"'`](?P<target>[^\"'`]+)[\"'`]\s*\)"), "dynamic import"),
        ]
        repo_resolved = repo_path.resolve()
        for line_number, line in enumerate(content.splitlines(), start=1):
            for pattern, import_type in patterns:
                for match in pattern.finditer(line):
                    target = match.group("target")
                    resolved = self.resolve_relative_import(path, target)
                    if resolved != target:
                        try:
                            resolved = Path(resolved).resolve().relative_to(repo_resolved).as_posix()
                        except ValueError:
                            pass
                    dependencies.append(
                        ModuleDependency(
                            source_file=relative,
                            target=target,
                            resolved_target=resolved,
                            import_type=import_type,
                            line=line_number,
                        )
                    )
        return dependencies

    def analyze(self, repo_path: Path) -> list[ModuleDependency]:
        self.build_file_index(repo_path)
        dependencies: list[ModuleDependency] = []
        seen: set[tuple[str, str, str, int]] = set()
        for path in self.files:
            suffix = path.suffix.lower()
            if suffix not in PYTHON_SUFFIXES | JAVASCRIPT_SUFFIXES:
                continue
            relative = path.relative_to(repo_path).as_posix()
            if suffix in PYTHON_SUFFIXES:
                findings = self.analyze_python_file(repo_path, path, relative)
            else:
                findings = self.analyze_javascript_file(repo_path, path, relative)
            for finding in findings:
                key = (finding.source_file, finding.target, finding.import_type, finding.line)
                if key in seen:
                    continue
                seen.add(key)
                dependencies.append(finding)
        return dependencies[:240]
