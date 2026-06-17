from pydantic import BaseModel, Field, HttpUrl


class HealthResponse(BaseModel):
    status: str


class RepositoryLoadRequest(BaseModel):
    github_url: HttpUrl


class RepositoryLoadResponse(BaseModel):
    repository_id: str
    message: str
    files_indexed: int
    chunks_indexed: int


class ChatRequest(BaseModel):
    repository_id: str = Field(..., min_length=1)
    question: str = Field(..., min_length=1)


class Source(BaseModel):
    file_path: str
    chunk_index: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
