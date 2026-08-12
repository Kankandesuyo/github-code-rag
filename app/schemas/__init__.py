from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.schemas.report_schema import (
    AgentLog,
    ApiEndpoint,
    DatabaseFinding,
    EntrypointFinding,
    FunctionCall,
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


class RepositorySummaryResponse(BaseModel):
    repository_id: str
    owner_id: str | None = None
    status: str
    files_indexed: int
    chunks_indexed: int
    created_at: int | None = None
    updated_at: int | None = None
    github_url: str | None = None
    default_branch: str | None = None
    source: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    display_name: str | None = None
    upload_name: str | None = None


class RepositoryListResponse(BaseModel):
    repositories: list[str]
    items: list[RepositorySummaryResponse]


class RepositoryDeleteResponse(BaseModel):
    repository_id: str
    deleted: bool
    collections_deleted: int


class ChatRequest(BaseModel):
    repository_id: str = Field(..., min_length=1, max_length=140, pattern=r"^[A-Za-z0-9_.-]+$")
    question: str = Field(..., min_length=1, max_length=4000)


class OnlineChatRequest(BaseModel):
    github_url: HttpUrl = Field(..., max_length=300)
    question: str = Field(..., min_length=1, max_length=4000)

    model_config = ConfigDict(extra="forbid")


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


class OnlineChatResponse(ChatResponse):
    mode: Literal["online"] = "online"
    repository_saved: Literal[False] = False
    repository_id: str
    files_scanned: int
    chunks_scanned: int
