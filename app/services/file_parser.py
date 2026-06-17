from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import get_settings
from app.utils.file_utils import safe_relative_path, should_ignore_dir, should_ignore_file


@dataclass
class ParsedFile:
    file_path: str
    content: str


@dataclass
class DocumentChunk:
    content: str
    metadata: dict


def read_repository_files(repo_path: Path) -> list[ParsedFile]:
    settings = get_settings()
    parsed_files: list[ParsedFile] = []

    for path in repo_path.rglob("*"):
        if any(should_ignore_dir(parent) for parent in path.relative_to(repo_path).parents):
            continue
        if path.is_dir():
            continue
        if should_ignore_file(path):
            continue
        if path.stat().st_size > settings.max_file_size_bytes:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                content = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
        except OSError:
            continue

        if content.strip():
            parsed_files.append(
                ParsedFile(file_path=safe_relative_path(path, repo_path), content=content)
            )

    return parsed_files


def split_files_into_chunks(files: list[ParsedFile], repository_id: str) -> list[DocumentChunk]:
    settings = get_settings()
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=[
            "\nclass ",
            "\ndef ",
            "\nfunction ",
            "\nexport ",
            "\n# ",
            "\n## ",
            "\n\n",
            "\n",
            " ",
            "",
        ],
    )

    chunks: list[DocumentChunk] = []
    for parsed_file in files:
        text_chunks = splitter.split_text(parsed_file.content)
        for index, chunk_text in enumerate(text_chunks):
            chunks.append(
                DocumentChunk(
                    content=chunk_text,
                    metadata={
                        "repository_id": repository_id,
                        "file_path": parsed_file.file_path,
                        "chunk_index": index,
                    },
                )
            )

    return chunks
