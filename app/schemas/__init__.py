from pydantic import BaseModel, Field, HttpUrl

from app.schemas.report_schema import (
    AgentLog,
    ApiEndpoint,
    DatabaseFinding,
    EntrypointFinding,
    GenerateReadmeRequest,
    GenerateReadmeResponse,
    ModuleDependency,
    ProjectReportResponse,
    RepositoryAnalysis,
    SourceRef,
    TechStackResult,
)


class HealthResponse(BaseModel):
    status: str


class RepositoryLoadRequest(BaseModel):
    github_url: HttpUrl = Field(..., max_length=300)


class RepositoryLoadResponse(BaseModel):
    repository_id: str
    message: str
    files_indexed: int
    chunks_indexed: int
    chunks_written: int = 0
    index_cached: bool = False
    changed_files_count: int = 0
    removed_files_count: int = 0


class ChatRequest(BaseModel):
    repository_id: str = Field(..., min_length=1, max_length=140, pattern=r"^[A-Za-z0-9_.-]+$")
    question: str = Field(..., min_length=1, max_length=4000)


class Source(BaseModel):
    file_path: str
    chunk_id: int
    chunk_index: int
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    symbol_name: str | None = None
    symbol_type: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
    logs: list[AgentLog] = Field(default_factory=list)
