from typing import Any, TypedDict

from app.agents.repository_agent import RepositoryAgent
from app.agents.supervisor_agent import SupervisorAgent
from app.agents.techstack_agent import TechStackAgent
from app.agents.writer_agent import WriterAgent
from app.schemas.report_schema import AgentLog

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # LangGraph is added as an optional dependency until installation.
    END = None
    StateGraph = None


class WorkflowState(TypedDict, total=False):
    task: str
    route: str
    repository_id: str
    repository: Any
    tech_stack: Any
    startup_hints: list[str]
    api_endpoints: list[Any]
    database_findings: list[Any]
    markdown: str
    project_overview: str
    startup_guide: str
    logs: list[AgentLog]


class CodebaseWorkflow:
    def __init__(self) -> None:
        self.supervisor = SupervisorAgent()
        self.repository_agent = RepositoryAgent()
        self.techstack_agent = TechStackAgent()
        self.writer_agent = WriterAgent()
        self.graph = self._build_graph() if StateGraph else None

    def _append_logs(self, state: WorkflowState, logs: list[AgentLog]) -> WorkflowState:
        return {**state, "logs": [*state.get("logs", []), *logs]}

    def _supervise_node(self, state: WorkflowState) -> WorkflowState:
        route, logs = self.supervisor.plan(state.get("task", "report"))
        return self._append_logs({**state, "route": route}, logs)

    def _repository_node(self, state: WorkflowState) -> WorkflowState:
        repository, logs = self.repository_agent.analyze(state["repository_id"])
        return self._append_logs({**state, "repository": repository}, logs)

    def _techstack_node(self, state: WorkflowState) -> WorkflowState:
        tech_stack, logs = self.techstack_agent.analyze(state["repository_id"])
        return self._append_logs({**state, "tech_stack": tech_stack}, logs)

    def _writer_node(self, state: WorkflowState) -> WorkflowState:
        if state.get("route") == "readme":
            markdown, logs = self.writer_agent.write_readme(
                repository_id=state["repository_id"],
                repository=state["repository"],
                tech_stack=state["tech_stack"],
                startup_hints=state.get("startup_hints", []),
                api_endpoints=state.get("api_endpoints", []),
            )
            return self._append_logs({**state, "markdown": markdown}, logs)

        markdown, overview, startup_guide, logs = self.writer_agent.write_report(
            repository_id=state["repository_id"],
            repository=state["repository"],
            tech_stack=state["tech_stack"],
            startup_hints=state.get("startup_hints", []),
            api_endpoints=state.get("api_endpoints", []),
            database_findings=state.get("database_findings", []),
        )
        return self._append_logs(
            {
                **state,
                "markdown": markdown,
                "project_overview": overview,
                "startup_guide": startup_guide,
            },
            logs,
        )

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("supervisor_node", self._supervise_node)
        graph.add_node("repository_node", self._repository_node)
        graph.add_node("techstack_node", self._techstack_node)
        graph.add_node("writer_node", self._writer_node)
        graph.set_entry_point("supervisor_node")
        graph.add_edge("supervisor_node", "repository_node")
        graph.add_edge("repository_node", "techstack_node")
        graph.add_edge("techstack_node", "writer_node")
        graph.add_edge("writer_node", END)
        return graph.compile()

    def run(self, initial_state: WorkflowState) -> WorkflowState:
        if self.graph:
            return self.graph.invoke(initial_state)

        state = self._supervise_node(initial_state)
        state = self._repository_node(state)
        state = self._techstack_node(state)
        return self._writer_node(state)
