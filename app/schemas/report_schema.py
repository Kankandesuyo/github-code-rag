from pydantic import BaseModel, Field


class AgentLog(BaseModel):
    agent: str
    action: str
    duration_ms: float | None = None
    cached: bool | None = None


class SourceRef(BaseModel):
    file_path: str
    chunk_id: int
    start_line: int | None = None
    end_line: int | None = None


class TechStackResult(BaseModel):
    backend: list[str] = Field(default_factory=list)
    frontend: list[str] = Field(default_factory=list)
    database: list[str] = Field(default_factory=list)
    devops: list[str] = Field(default_factory=list)
    ai: list[str] = Field(default_factory=list)


class RepositoryAnalysis(BaseModel):
    file_count: int
    directory_count: int
    languages: list[str]
    largest_files: list[dict]
    entry_points: list[str]
    directory_tree: str
    core_modules: list[dict]


class ApiEndpoint(BaseModel):
    framework: str
    method: str
    path: str
    handler: str = ""
    file_path: str
    line: int


class DatabaseFinding(BaseModel):
    technology: str
    file_path: str
    line: int
    detail: str
    model_name: str = ""
    fields: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)


class EntrypointFinding(BaseModel):
    kind: str
    file_path: str
    reason: str


class ModuleDependency(BaseModel):
    source_file: str
    target: str
    resolved_target: str = ""
    import_type: str
    line: int


class FunctionCall(BaseModel):
    caller_file: str
    caller_symbol: str
    callee_file: str
    callee_symbol: str
    call_type: str
    line: int


class ProjectReportResponse(BaseModel):
    repository_id: str
    markdown: str
    project_overview: str
    technology_stack: TechStackResult
    startup_guide: str
    directory_structure: str
    core_modules: list[dict]
    api_analysis: list[ApiEndpoint]
    database_analysis: list[DatabaseFinding]
    entrypoint_analysis: list[EntrypointFinding] = Field(default_factory=list)
    dependency_analysis: list[ModuleDependency] = Field(default_factory=list)
    function_call_analysis: list[FunctionCall] = Field(default_factory=list)
    environment_variables: list[str] = Field(default_factory=list)
    deployment_method: list[str] = Field(default_factory=list)
    logs: list[AgentLog]


class GenerateReadmeRequest(BaseModel):
    repository_id: str = Field(..., min_length=1, max_length=140, pattern=r"^[A-Za-z0-9_.-]+$")


class GenerateReadmeResponse(BaseModel):
    repository_id: str
    markdown: str
    logs: list[AgentLog]
