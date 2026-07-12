import time
from app.analyzers.techstack_analyzer import TechStackAnalyzer
from app.agents.repository_agent import RepositoryAgent
from app.schemas.report_schema import AgentLog, TechStackResult


class TechStackAgent:
    name = "TechStackAgent"

    def __init__(self) -> None:
        self.analyzer = TechStackAnalyzer()
        self.repository_agent = RepositoryAgent()

    def resolve_repo_path(self, repository_id: str):
        return self.repository_agent.resolve_repo_path(repository_id)

    def analyze(self, repository_id: str) -> tuple[TechStackResult, list[AgentLog]]:
        started = time.perf_counter()
        repo_path = self.resolve_repo_path(repository_id)
        result = self.analyzer.analyze_path(repo_path)
        logs = [
            AgentLog(
                agent=self.name,
                action="Detecting technology stack",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
        return result, logs
