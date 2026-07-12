import time

from app.schemas.report_schema import (
    AgentLog,
    ApiEndpoint,
    DatabaseFinding,
    EntrypointFinding,
    ModuleDependency,
    RepositoryAnalysis,
    TechStackResult,
)


class WriterAgent:
    name = "WriterAgent"

    def summarize_stack(self, tech_stack: TechStackResult) -> str:
        lines: list[str] = []
        for category, values in tech_stack.model_dump().items():
            display = ", ".join(values) if values else "Not detected"
            lines.append(f"- {category}: {display}")
        return "\n".join(lines)

    def write_project_overview(self, repository_id: str, repository: RepositoryAnalysis, tech_stack: TechStackResult) -> str:
        stack_values = [item for values in tech_stack.model_dump().values() for item in values]
        stack_text = ", ".join(stack_values[:8]) if stack_values else "no dominant framework detected"
        entry_text = ", ".join(repository.entry_points[:5]) if repository.entry_points else "no obvious entry point detected"
        return (
            f"`{repository_id}` contains {repository.file_count} readable files across "
            f"{repository.directory_count} directories. Detected stack: {stack_text}. "
            f"Likely entry points: {entry_text}."
        )

    def write_startup_guide(self, startup_hints: list[str]) -> str:
        if not startup_hints:
            return "No explicit startup guide was found. Check README, package manager files, or container config."
        return "\n".join(f"- {hint}" for hint in startup_hints)

    def write_api_analysis(self, endpoints: list[ApiEndpoint]) -> str:
        if not endpoints:
            return "No FastAPI, Flask, or Express routes were detected by static analysis."
        return "\n".join(
            f"- {endpoint.framework} `{endpoint.method}` `{endpoint.path}` "
            f"handler=`{endpoint.handler or 'unknown'}` ({endpoint.file_path}:{endpoint.line})"
            for endpoint in endpoints
        )

    def write_database_analysis(self, findings: list[DatabaseFinding]) -> str:
        if not findings:
            return "No SQLAlchemy, Django ORM, Prisma, or Mongoose definitions were detected by static analysis."
        lines: list[str] = []
        for finding in findings:
            extras = []
            if finding.model_name:
                extras.append(f"model={finding.model_name}")
            if finding.fields:
                extras.append("fields=" + ", ".join(finding.fields))
            if finding.relationships:
                extras.append("relationships=" + ", ".join(finding.relationships))
            suffix = " [" + "; ".join(extras) + "]" if extras else ""
            lines.append(f"- {finding.technology}: {finding.detail}{suffix} ({finding.file_path}:{finding.line})")
        return "\n".join(lines)

    def write_entrypoint_analysis(self, entrypoints: list[EntrypointFinding]) -> str:
        if not entrypoints:
            return "No obvious startup, application, or configuration entry points were detected."
        return "\n".join(
            f"- {entrypoint.kind}: `{entrypoint.file_path}` - {entrypoint.reason}"
            for entrypoint in entrypoints
        )

    def write_dependency_analysis(self, dependencies: list[ModuleDependency]) -> str:
        if not dependencies:
            return "No Python or JS/TS module dependencies were detected by static analysis."
        return "\n".join(
            f"- `{dependency.source_file}` -> `{dependency.resolved_target or dependency.target}` "
            f"via {dependency.import_type} ({dependency.source_file}:{dependency.line})"
            for dependency in dependencies[:80]
        )

    def write_environment_variables(self, variables: list[str]) -> str:
        if not variables:
            return "No explicit environment variables were detected from env files, README, or compose files."
        return "\n".join(f"- `{variable}`" for variable in variables)

    def write_deployment_method(self, methods: list[str]) -> str:
        if not methods:
            return "No explicit deployment method was detected."
        return "\n".join(f"- {method}" for method in methods)

    def write_report(
        self,
        *,
        repository_id: str,
        repository: RepositoryAnalysis,
        tech_stack: TechStackResult,
        startup_hints: list[str],
        api_endpoints: list[ApiEndpoint],
        database_findings: list[DatabaseFinding],
        entrypoints: list[EntrypointFinding] | None = None,
        dependencies: list[ModuleDependency] | None = None,
        environment_variables: list[str] | None = None,
        deployment_methods: list[str] | None = None,
    ) -> tuple[str, str, str, list[AgentLog]]:
        started = time.perf_counter()
        entrypoints = entrypoints or []
        dependencies = dependencies or []
        environment_variables = environment_variables or []
        deployment_methods = deployment_methods or []
        overview = self.write_project_overview(repository_id, repository, tech_stack)
        startup_guide = self.write_startup_guide(startup_hints)
        api_analysis = self.write_api_analysis(api_endpoints)
        database_analysis = self.write_database_analysis(database_findings)
        entrypoint_analysis = self.write_entrypoint_analysis(entrypoints)
        dependency_analysis = self.write_dependency_analysis(dependencies)
        environment_analysis = self.write_environment_variables(environment_variables)
        deployment_analysis = self.write_deployment_method(deployment_methods)
        core_modules = "\n".join(
            f"- `{module['path']}`: {module['file_count']} files, languages={', '.join(module['languages'])}"
            for module in repository.core_modules
        ) or "No core modules detected."

        markdown = "\n\n".join(
            [
                "# Project Overview\n\n" + overview,
                "# Technology Stack\n\n" + self.summarize_stack(tech_stack),
                "# Startup Guide\n\n" + startup_guide,
                "# Directory Structure\n\n```text\n" + repository.directory_tree + "\n```",
                "# Core Modules\n\n" + core_modules,
                "# API Analysis\n\n" + api_analysis,
                "# Database Analysis\n\n" + database_analysis,
                "# Entrypoint Analysis\n\n" + entrypoint_analysis,
                "# Module Dependency Analysis\n\n" + dependency_analysis,
                "# Environment Variables\n\n" + environment_analysis,
                "# Deployment Method\n\n" + deployment_analysis,
            ]
        )
        logs = [
            AgentLog(
                agent=self.name,
                action="Generating project report",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
        return markdown, overview, startup_guide, logs

    def write_readme(
        self,
        *,
        repository_id: str,
        repository: RepositoryAnalysis,
        tech_stack: TechStackResult,
        startup_hints: list[str],
        api_endpoints: list[ApiEndpoint],
    ) -> tuple[str, list[AgentLog]]:
        started = time.perf_counter()
        features = [
            f"Readable files indexed: {repository.file_count}",
            f"Detected languages: {', '.join(repository.languages) if repository.languages else 'unknown'}",
        ]
        if repository.entry_points:
            features.append("Entry points: " + ", ".join(repository.entry_points[:5]))

        api_section = self.write_api_analysis(api_endpoints)
        markdown = "\n\n".join(
            [
                f"# {repository_id}",
                "## Project Overview\n\n" + self.write_project_overview(repository_id, repository, tech_stack),
                "## Features\n\n" + "\n".join(f"- {feature}" for feature in features),
                "## Tech Stack\n\n" + self.summarize_stack(tech_stack),
                "## Installation\n\nInstall dependencies according to the detected package files such as `requirements.txt`, `pyproject.toml`, or `package.json`.",
                "## Startup\n\n" + self.write_startup_guide(startup_hints),
                "## API\n\n" + api_section,
                "## Directory Structure\n\n```text\n" + repository.directory_tree + "\n```",
            ]
        )
        logs = [
            AgentLog(
                agent=self.name,
                action="Generating README markdown",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
        return markdown, logs
