import json
import re
import time
from pathlib import Path

from app.analyzers.api_analyzer import APIAnalyzer
from app.analyzers.database_analyzer import DatabaseAnalyzer
from app.analyzers.dependency_analyzer import DependencyAnalyzer
from app.analyzers.entrypoint_analyzer import EntrypointAnalyzer
from app.agents.repository_agent import RepositoryAgent
from app.graph.workflow import CodebaseWorkflow
from app.schemas.report_schema import (
    AgentLog,
    ApiEndpoint,
    DatabaseFinding,
    EntrypointFinding,
    GenerateReadmeResponse,
    ModuleDependency,
    ProjectReportResponse,
    RepositoryAnalysis,
    TechStackResult,
)
from app.services.manifest_service import RepositoryManifestCache


class ReportService:
    def __init__(self) -> None:
        self.repository_agent = RepositoryAgent()
        self.workflow = CodebaseWorkflow()
        self.manifest_cache = RepositoryManifestCache()

    def resolve_repo_path(self, repository_id: str) -> Path:
        return self.repository_agent.resolve_repo_path(repository_id)

    def read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def collect_startup_hints(self, repo_path: Path) -> list[str]:
        started = time.perf_counter()
        hints: list[str] = []
        package_json = repo_path / "package.json"
        if package_json.exists():
            try:
                parsed = json.loads(self.read_text_file(package_json))
                scripts = parsed.get("scripts", {})
                for name in ("dev", "start", "serve", "build"):
                    if name in scripts:
                        hints.append(f"`npm run {name}` -> `{scripts[name]}`")
            except json.JSONDecodeError:
                pass

        if (repo_path / "requirements.txt").exists():
            hints.append("Python dependencies detected: install with `pip install -r requirements.txt`.")
        if (repo_path / "pyproject.toml").exists():
            hints.append("Python project metadata detected in `pyproject.toml`; use the configured build tool or `pip install -e .`.")
        if (repo_path / "manage.py").exists():
            hints.append("Django entry point detected: try `python manage.py runserver`.")
        if (repo_path / "app" / "main.py").exists():
            hints.append("FastAPI-style `app/main.py` detected: try `uvicorn app.main:app --reload`.")
        if (repo_path / "main.py").exists():
            hints.append("Python `main.py` detected: try `python main.py`.")
        if (repo_path / "Dockerfile").exists():
            hints.append("Dockerfile detected: build with `docker build -t <name> .` and run the resulting image.")
        if (repo_path / "docker-compose.yml").exists() or (repo_path / "docker-compose.yaml").exists():
            hints.append("Docker Compose config detected: try `docker compose up --build`.")
        return hints

    def timed_collect_startup_hints(self, repo_path: Path) -> tuple[list[str], AgentLog]:
        started = time.perf_counter()
        hints = self.collect_startup_hints(repo_path)
        return hints, AgentLog(
            agent="StartupAnalyzer",
            action="Collecting startup hints",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def iter_text_files(self, repo_path: Path) -> list[Path]:
        return self.repository_agent.iter_files(repo_path)

    def analyze_api(self, repo_path: Path) -> list[ApiEndpoint]:
        return APIAnalyzer(self.iter_text_files(repo_path)).analyze(repo_path)

    def timed_analyze_api(self, repo_path: Path) -> tuple[list[ApiEndpoint], AgentLog]:
        started = time.perf_counter()
        endpoints = self.analyze_api(repo_path)
        return endpoints, AgentLog(
            agent="APIAnalyzer",
            action="Detecting FastAPI routers, Flask routes, and Express routes",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def analyze_database(self, repo_path: Path) -> list[DatabaseFinding]:
        return DatabaseAnalyzer(self.iter_text_files(repo_path)).analyze(repo_path)

    def timed_analyze_database(self, repo_path: Path) -> tuple[list[DatabaseFinding], AgentLog]:
        started = time.perf_counter()
        findings = self.analyze_database(repo_path)
        return findings, AgentLog(
            agent="DatabaseAnalyzer",
            action="Detecting SQLAlchemy, Django ORM, Prisma, and Mongoose models",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def timed_analyze_entrypoints(self, repo_path: Path) -> tuple[list[EntrypointFinding], AgentLog]:
        started = time.perf_counter()
        entrypoints = EntrypointAnalyzer(self.iter_text_files(repo_path)).analyze(repo_path)
        return entrypoints, AgentLog(
            agent="EntrypointAnalyzer",
            action="Detecting startup, application, and configuration entry points",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def timed_analyze_dependencies(self, repo_path: Path) -> tuple[list[ModuleDependency], AgentLog]:
        started = time.perf_counter()
        dependencies = DependencyAnalyzer(self.iter_text_files(repo_path)).analyze(repo_path)
        return dependencies, AgentLog(
            agent="DependencyAnalyzer",
            action="Detecting Python and JS/TS module dependencies",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def timed_detect_environment_variables(self, repo_path: Path) -> tuple[list[str], AgentLog]:
        started = time.perf_counter()
        variables: set[str] = set()
        for relative in (".env.example", "README.md", "docker-compose.yml", "docker-compose.yaml"):
            path = repo_path / relative
            if not path.exists() or not path.is_file():
                continue
            content = self.read_text_file(path)
            for line in content.splitlines():
                match = re.match(r"\s*([A-Z][A-Z0-9_]{2,})\s*=", line)
                if match:
                    variables.add(match.group(1))
            for match in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", content):
                token = match.group(0)
                if any(marker in token for marker in ("KEY", "TOKEN", "SECRET", "URL", "HOST", "PORT", "DATABASE", "MODEL")):
                    variables.add(token)
        return sorted(variables)[:80], AgentLog(
            agent="EnvironmentAnalyzer",
            action="Detecting environment variables",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def timed_detect_deployment_methods(self, repo_path: Path) -> tuple[list[str], AgentLog]:
        started = time.perf_counter()
        methods: list[str] = []
        if (repo_path / "Dockerfile").exists():
            methods.append("Dockerfile")
        if (repo_path / "docker-compose.yml").exists() or (repo_path / "docker-compose.yaml").exists():
            methods.append("Docker Compose")
        if (repo_path / ".github" / "workflows").exists():
            methods.append("GitHub Actions")
        if (repo_path / "vercel.json").exists():
            methods.append("Vercel")
        if (repo_path / "netlify.toml").exists():
            methods.append("Netlify")
        if (repo_path / "Procfile").exists():
            methods.append("Procfile")
        return sorted(set(methods)), AgentLog(
            agent="DeploymentAnalyzer",
            action="Detecting deployment method",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def build_manifest_data(self, repository_id: str, repo_path: Path) -> dict:
        repository, repository_logs = self.repository_agent.analyze(repository_id)
        tech_stack, tech_logs = self.workflow.techstack_agent.analyze(repository_id)
        startup_hints, startup_log = self.timed_collect_startup_hints(repo_path)
        api_endpoints, api_log = self.timed_analyze_api(repo_path)
        database_findings, database_log = self.timed_analyze_database(repo_path)
        entrypoints, entrypoint_log = self.timed_analyze_entrypoints(repo_path)
        dependencies, dependency_log = self.timed_analyze_dependencies(repo_path)
        env_vars, env_log = self.timed_detect_environment_variables(repo_path)
        deployment_methods, deployment_log = self.timed_detect_deployment_methods(repo_path)
        logs = [
            *repository_logs,
            *tech_logs,
            startup_log,
            api_log,
            database_log,
            entrypoint_log,
            dependency_log,
            env_log,
            deployment_log,
        ]
        return {
            "repository": repository.model_dump(),
            "tech_stack": tech_stack.model_dump(),
            "startup_hints": startup_hints,
            "api_endpoints": [endpoint.model_dump() for endpoint in api_endpoints],
            "database_findings": [finding.model_dump() for finding in database_findings],
            "entrypoints": [entrypoint.model_dump() for entrypoint in entrypoints],
            "dependencies": [dependency.model_dump() for dependency in dependencies],
            "environment_variables": env_vars,
            "deployment_methods": deployment_methods,
            "analysis_logs": [log.model_dump() for log in logs],
        }

    def load_analysis_manifest(self, repository_id: str) -> tuple[dict, list[AgentLog]]:
        repo_path = self.resolve_repo_path(repository_id)

        def builder() -> dict:
            return self.build_manifest_data(repository_id, repo_path)

        data, cache_logs = self.manifest_cache.get_or_build(repo_path=repo_path, builder=builder)
        analysis_logs = [AgentLog.model_validate(log) for log in data.get("analysis_logs", [])]
        if cache_logs and cache_logs[0].cached:
            analysis_logs = [
                AgentLog(
                    agent=log.agent,
                    action=f"Using cached result: {log.action}",
                    duration_ms=0.0,
                    cached=True,
                )
                for log in analysis_logs
            ]
        return data, [*cache_logs, *analysis_logs]

    def unpack_manifest(self, data: dict) -> tuple[RepositoryAnalysis, TechStackResult, list[str], list[ApiEndpoint], list[DatabaseFinding], list[EntrypointFinding], list[ModuleDependency], list[str], list[str]]:
        return (
            RepositoryAnalysis.model_validate(data["repository"]),
            TechStackResult.model_validate(data["tech_stack"]),
            list(data.get("startup_hints", [])),
            [ApiEndpoint.model_validate(endpoint) for endpoint in data.get("api_endpoints", [])],
            [DatabaseFinding.model_validate(finding) for finding in data.get("database_findings", [])],
            [EntrypointFinding.model_validate(entrypoint) for entrypoint in data.get("entrypoints", [])],
            [ModuleDependency.model_validate(dependency) for dependency in data.get("dependencies", [])],
            list(data.get("environment_variables", [])),
            list(data.get("deployment_methods", [])),
        )

    def build_project_report(self, repository_id: str) -> ProjectReportResponse:
        _, supervisor_logs = self.workflow.supervisor.plan("report")
        data, manifest_logs = self.load_analysis_manifest(repository_id)
        repository, tech_stack, startup_hints, api_endpoints, database_findings, entrypoints, dependencies, env_vars, deployment_methods = self.unpack_manifest(data)
        markdown, overview, startup_guide, writer_logs = self.workflow.writer_agent.write_report(
            repository_id=repository_id,
            repository=repository,
            tech_stack=tech_stack,
            startup_hints=startup_hints,
            api_endpoints=api_endpoints,
            database_findings=database_findings,
            entrypoints=entrypoints,
            dependencies=dependencies,
            environment_variables=env_vars,
            deployment_methods=deployment_methods,
        )
        return ProjectReportResponse(
            repository_id=repository_id,
            markdown=markdown,
            project_overview=overview,
            technology_stack=tech_stack,
            startup_guide=startup_guide,
            directory_structure=repository.directory_tree,
            core_modules=repository.core_modules,
            api_analysis=api_endpoints,
            database_analysis=database_findings,
            entrypoint_analysis=entrypoints,
            dependency_analysis=dependencies,
            environment_variables=env_vars,
            deployment_method=deployment_methods,
            logs=[*supervisor_logs, *manifest_logs, *writer_logs],
        )

    def generate_readme(self, repository_id: str) -> GenerateReadmeResponse:
        _, supervisor_logs = self.workflow.supervisor.plan("readme")
        data, manifest_logs = self.load_analysis_manifest(repository_id)
        repository, tech_stack, startup_hints, api_endpoints, _database_findings, _entrypoints, _dependencies, _env_vars, _deployment_methods = self.unpack_manifest(data)
        markdown, writer_logs = self.workflow.writer_agent.write_readme(
            repository_id=repository_id,
            repository=repository,
            tech_stack=tech_stack,
            startup_hints=startup_hints,
            api_endpoints=api_endpoints,
        )
        return GenerateReadmeResponse(
            repository_id=repository_id,
            markdown=markdown,
            logs=[*supervisor_logs, *manifest_logs, *writer_logs],
        )
