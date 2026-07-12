import re
from pathlib import Path

from app.schemas.report_schema import ApiEndpoint


HTTP_METHODS = "get|post|put|patch|delete|options|head"
PYTHON_SUFFIXES = {".py"}
JAVASCRIPT_SUFFIXES = {".js", ".ts", ".tsx", ".jsx"}


def join_route_path(*parts: str) -> str:
    cleaned = [part.strip("/") for part in parts if part and part != "/"]
    if not cleaned:
        return "/"
    return "/" + "/".join(cleaned)


def find_next_handler(lines: list[str], decorator_index: int) -> str:
    for line in lines[decorator_index: decorator_index + 8]:
        match = re.match(r"\s*(?:async\s+)?def\s+(\w+)\s*\(", line)
        if match:
            return match.group(1)
    return ""


def unique_api_endpoints(endpoints: list[ApiEndpoint]) -> list[ApiEndpoint]:
    seen: set[tuple[str, str, str, str, int, str]] = set()
    unique: list[ApiEndpoint] = []
    for endpoint in endpoints:
        key = (endpoint.framework, endpoint.method, endpoint.path, endpoint.file_path, endpoint.line, endpoint.handler)
        if key in seen:
            continue
        seen.add(key)
        unique.append(endpoint)
    return unique


class APIAnalyzer:
    def __init__(self, files: list[Path]) -> None:
        self.files = files

    def read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def detect_fastapi_prefixes(self, content: str) -> tuple[dict[str, str], dict[str, str]]:
        router_prefixes: dict[str, str] = {}
        app_router_prefixes: dict[str, str] = {}
        router_assign_pattern = re.compile(
            r"(?P<var>\w+)\s*=\s*(?:fastapi\.)?APIRouter\([^)]*prefix\s*=\s*[\"'](?P<prefix>[^\"']+)[\"']",
            re.IGNORECASE | re.DOTALL,
        )
        include_router_pattern = re.compile(
            r"\.include_router\(\s*(?P<var>\w+)(?:[^)]*?prefix\s*=\s*[\"'](?P<prefix>[^\"']+)[\"'])?",
            re.IGNORECASE | re.DOTALL,
        )
        for match in router_assign_pattern.finditer(content):
            router_prefixes[match.group("var")] = match.group("prefix")
        for match in include_router_pattern.finditer(content):
            app_router_prefixes[match.group("var")] = match.group("prefix") or ""
        return router_prefixes, app_router_prefixes

    def analyze_python_api_file(self, content: str, relative: str) -> list[ApiEndpoint]:
        endpoints: list[ApiEndpoint] = []
        router_prefixes, app_router_prefixes = self.detect_fastapi_prefixes(content)
        fastapi_pattern = re.compile(
            rf"@(?P<target>\w+)\.(?P<method>{HTTP_METHODS})\(\s*[\"'](?P<path>[^\"']+)[\"']",
            re.IGNORECASE,
        )
        flask_pattern = re.compile(
            r"@(?P<target>\w+)\.route\(\s*[\"'](?P<path>[^\"']+)[\"'](?:,\s*methods\s*=\s*\[(?P<methods>[^\]]+)\])?",
            re.IGNORECASE,
        )
        blueprint_pattern = re.compile(
            r"(?P<var>\w+)\s*=\s*Blueprint\([^)]*url_prefix\s*=\s*[\"'](?P<prefix>[^\"']+)[\"']",
            re.IGNORECASE | re.DOTALL,
        )
        flask_prefixes = {match.group("var"): match.group("prefix") for match in blueprint_pattern.finditer(content)}
        lines = content.splitlines()

        for line_number, line in enumerate(lines, start=1):
            for match in fastapi_pattern.finditer(line):
                target = match.group("target")
                router_prefix = router_prefixes.get(target, "")
                include_prefix = app_router_prefixes.get(target, "")
                endpoints.append(
                    ApiEndpoint(
                        framework="FastAPI",
                        method=match.group("method").upper(),
                        path=join_route_path(include_prefix, router_prefix, match.group("path")),
                        handler=find_next_handler(lines, line_number),
                        file_path=relative,
                        line=line_number,
                    )
                )
            for match in flask_pattern.finditer(line):
                raw_methods = match.group("methods") or "'GET'"
                methods = re.findall(r"[A-Z]+", raw_methods.upper()) or ["GET"]
                prefix = flask_prefixes.get(match.group("target"), "")
                for method in methods:
                    endpoints.append(
                        ApiEndpoint(
                            framework="Flask",
                            method=method,
                            path=join_route_path(prefix, match.group("path")),
                            handler=find_next_handler(lines, line_number),
                            file_path=relative,
                            line=line_number,
                        )
                    )
        return endpoints

    def analyze_javascript_api_file(self, content: str, relative: str) -> list[ApiEndpoint]:
        endpoints: list[ApiEndpoint] = []
        express_pattern = re.compile(
            rf"\b(?P<target>app|router)\.(?P<method>{HTTP_METHODS})\(\s*[\"'`](?P<path>[^\"'`]+)[\"'`]\s*,\s*(?P<handler>[A-Za-z_$][\w$]*)?",
            re.IGNORECASE,
        )
        express_route_chain_pattern = re.compile(
            rf"\b(?:app|router)\.route\(\s*[\"'`](?P<path>[^\"'`]+)[\"'`]\s*\)(?P<chain>(?:\s*\.(?:{HTTP_METHODS})\([^)]*\))+)",
            re.IGNORECASE,
        )
        router_prefix_pattern = re.compile(
            r"\bapp\.use\(\s*[\"'`](?P<prefix>[^\"'`]+)[\"'`]\s*,\s*(?P<router>\w+)",
            re.IGNORECASE,
        )
        router_prefixes = {match.group("router"): match.group("prefix") for match in router_prefix_pattern.finditer(content)}

        for line_number, line in enumerate(content.splitlines(), start=1):
            for match in express_pattern.finditer(line):
                endpoints.append(
                    ApiEndpoint(
                        framework="Express",
                        method=match.group("method").upper(),
                        path=match.group("path"),
                        handler=match.group("handler") or "",
                        file_path=relative,
                        line=line_number,
                    )
                )
            for match in express_route_chain_pattern.finditer(line):
                for method in re.findall(rf"\.({HTTP_METHODS})\(", match.group("chain"), flags=re.IGNORECASE):
                    endpoints.append(
                        ApiEndpoint(
                            framework="Express",
                            method=method.upper(),
                            path=match.group("path"),
                            handler="",
                            file_path=relative,
                            line=line_number,
                        )
                    )
            for router, prefix in router_prefixes.items():
                prefixed_pattern = re.compile(
                    rf"\b{re.escape(router)}\.(?P<method>{HTTP_METHODS})\(\s*[\"'`](?P<path>[^\"'`]+)[\"'`]\s*,\s*(?P<handler>[A-Za-z_$][\w$]*)?",
                    re.IGNORECASE,
                )
                for match in prefixed_pattern.finditer(line):
                    endpoints.append(
                        ApiEndpoint(
                            framework="Express",
                            method=match.group("method").upper(),
                            path=join_route_path(prefix, match.group("path")),
                            handler=match.group("handler") or "",
                            file_path=relative,
                            line=line_number,
                        )
                    )
        return endpoints

    def analyze(self, repo_path: Path) -> list[ApiEndpoint]:
        endpoints: list[ApiEndpoint] = []
        for path in self.files:
            suffix = path.suffix.lower()
            if suffix not in PYTHON_SUFFIXES | JAVASCRIPT_SUFFIXES:
                continue
            content = self.read_text_file(path)
            relative = path.relative_to(repo_path).as_posix()
            if suffix in PYTHON_SUFFIXES:
                endpoints.extend(self.analyze_python_api_file(content, relative))
            if suffix in JAVASCRIPT_SUFFIXES:
                endpoints.extend(self.analyze_javascript_api_file(content, relative))
        return unique_api_endpoints(endpoints)[:160]
