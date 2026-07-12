import time

from app.schemas.report_schema import AgentLog


class SupervisorAgent:
    name = "SupervisorAgent"

    def plan(self, task: str) -> tuple[str, list[AgentLog]]:
        started = time.perf_counter()
        normalized = task.lower().strip()
        if "readme" in normalized:
            action = "Planning README generation workflow"
            route = "readme"
        elif "report" in normalized or "analysis" in normalized:
            action = "Planning repository report workflow"
            route = "report"
        else:
            action = "Planning RAG answer workflow"
            route = "rag"
        return route, [
            AgentLog(
                agent=self.name,
                action=action,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        ]
