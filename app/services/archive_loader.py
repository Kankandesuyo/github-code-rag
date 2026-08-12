from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile
import time
import unicodedata
from urllib.parse import unquote_to_bytes
import uuid
import zipfile

from app.config import Settings, get_settings
from app.services.file_parser import (
    DocumentChunk,
    ParsedFile,
    read_repository_files,
    split_files_into_chunks,
)
from app.services.repo_loader import ImportBudget
from app.utils.file_utils import should_ignore_dir, should_ignore_file


SOURCE_MANIFEST_FILE = "source_manifest.json"
SOURCE_MANIFEST_VERSION = 1
ARCHIVE_SOURCE = "zip_upload"
COPY_CHUNK_SIZE = 64 * 1024
EOCD_MIN_SIZE = 22
EOCD_MAX_SEARCH = EOCD_MIN_SIZE + 65_535
EOCD_STRUCT = struct.Struct("<4s4H2LH")
EOCD_SIGNATURE = b"PK\x05\x06"
ALLOWED_COMPRESSION_METHODS = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:")
WINDOWS_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ArchiveImportErrorCode(str, Enum):
    INVALID_CONTENT_TYPE = "invalid_content_type"
    INVALID_ARCHIVE_NAME = "invalid_archive_name"
    INVALID_CONTENT_LENGTH = "invalid_content_length"
    UPLOAD_TOO_LARGE = "upload_too_large"
    UPLOAD_TIMEOUT = "upload_timeout"
    EMPTY_UPLOAD = "empty_upload"
    INVALID_ZIP = "invalid_zip"
    ENCRYPTED_ARCHIVE = "encrypted_archive"
    UNSUPPORTED_COMPRESSION = "unsupported_compression"
    UNSAFE_ENTRY_PATH = "unsafe_entry_path"
    SPECIAL_FILE = "special_file"
    FILE_LIMIT_EXCEEDED = "file_limit_exceeded"
    DIRECTORY_LIMIT_EXCEEDED = "directory_limit_exceeded"
    UNCOMPRESSED_LIMIT_EXCEEDED = "uncompressed_limit_exceeded"
    COMPRESSION_RATIO_EXCEEDED = "compression_ratio_exceeded"
    PATH_DEPTH_EXCEEDED = "path_depth_exceeded"
    PATH_LENGTH_EXCEEDED = "path_length_exceeded"
    CENTRAL_DIRECTORY_LIMIT_EXCEEDED = "central_directory_limit_exceeded"
    NO_SUPPORTED_FILES = "no_supported_files"
    UPLOAD_INTERRUPTED = "upload_interrupted"
    STORAGE_FAILURE = "storage_failure"


ARCHIVE_ERROR_DETAILS: dict[ArchiveImportErrorCode, str] = {
    ArchiveImportErrorCode.INVALID_CONTENT_TYPE: "Content-Type must be application/zip",
    ArchiveImportErrorCode.INVALID_ARCHIVE_NAME: "X-Archive-Name must be a valid ZIP filename",
    ArchiveImportErrorCode.INVALID_CONTENT_LENGTH: "invalid Content-Length header",
    ArchiveImportErrorCode.UPLOAD_TOO_LARGE: "ZIP upload exceeds the configured size limit",
    ArchiveImportErrorCode.UPLOAD_TIMEOUT: "ZIP upload exceeded the import deadline",
    ArchiveImportErrorCode.EMPTY_UPLOAD: "ZIP upload body is empty",
    ArchiveImportErrorCode.INVALID_ZIP: "invalid ZIP archive",
    ArchiveImportErrorCode.ENCRYPTED_ARCHIVE: "encrypted ZIP archives are not supported",
    ArchiveImportErrorCode.UNSUPPORTED_COMPRESSION: "ZIP archive uses an unsupported compression method",
    ArchiveImportErrorCode.UNSAFE_ENTRY_PATH: "ZIP archive contains an unsafe entry path",
    ArchiveImportErrorCode.SPECIAL_FILE: "ZIP archive contains a symbolic link or special file",
    ArchiveImportErrorCode.FILE_LIMIT_EXCEEDED: "ZIP archive exceeds the configured file limit",
    ArchiveImportErrorCode.DIRECTORY_LIMIT_EXCEEDED: "ZIP archive exceeds the configured directory limit",
    ArchiveImportErrorCode.UNCOMPRESSED_LIMIT_EXCEEDED: "ZIP archive exceeds the configured expanded-size limit",
    ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED: "ZIP archive exceeds the configured compression-ratio limit",
    ArchiveImportErrorCode.PATH_DEPTH_EXCEEDED: "ZIP archive exceeds the configured path-depth limit",
    ArchiveImportErrorCode.PATH_LENGTH_EXCEEDED: "ZIP archive exceeds the configured path-length limit",
    ArchiveImportErrorCode.CENTRAL_DIRECTORY_LIMIT_EXCEEDED: "ZIP archive exceeds the configured metadata limit",
    ArchiveImportErrorCode.NO_SUPPORTED_FILES: "ZIP archive contains no supported files",
    ArchiveImportErrorCode.UPLOAD_INTERRUPTED: "ZIP upload was interrupted",
    ArchiveImportErrorCode.STORAGE_FAILURE: "ZIP import failed",
}

ARCHIVE_CLIENT_ERROR_CODES = {
    ArchiveImportErrorCode.INVALID_CONTENT_TYPE,
    ArchiveImportErrorCode.INVALID_ARCHIVE_NAME,
    ArchiveImportErrorCode.INVALID_CONTENT_LENGTH,
    ArchiveImportErrorCode.EMPTY_UPLOAD,
    ArchiveImportErrorCode.INVALID_ZIP,
    ArchiveImportErrorCode.ENCRYPTED_ARCHIVE,
    ArchiveImportErrorCode.UNSAFE_ENTRY_PATH,
    ArchiveImportErrorCode.SPECIAL_FILE,
    ArchiveImportErrorCode.NO_SUPPORTED_FILES,
    ArchiveImportErrorCode.UPLOAD_INTERRUPTED,
    ArchiveImportErrorCode.UPLOAD_TIMEOUT,
    ArchiveImportErrorCode.UNSUPPORTED_COMPRESSION,
}

ARCHIVE_LIMIT_ERROR_CODES = {
    ArchiveImportErrorCode.UPLOAD_TOO_LARGE,
    ArchiveImportErrorCode.FILE_LIMIT_EXCEEDED,
    ArchiveImportErrorCode.DIRECTORY_LIMIT_EXCEEDED,
    ArchiveImportErrorCode.UNCOMPRESSED_LIMIT_EXCEEDED,
    ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED,
    ArchiveImportErrorCode.PATH_DEPTH_EXCEEDED,
    ArchiveImportErrorCode.PATH_LENGTH_EXCEEDED,
    ArchiveImportErrorCode.CENTRAL_DIRECTORY_LIMIT_EXCEEDED,
}


class ArchiveImportError(RuntimeError):
    def __init__(self, code: ArchiveImportErrorCode):
        self.code = code
        self.public_detail = ARCHIVE_ERROR_DETAILS[code]
        if code in ARCHIVE_LIMIT_ERROR_CODES:
            self.status_code = 413
        elif code == ArchiveImportErrorCode.INVALID_CONTENT_TYPE:
            self.status_code = 415
        elif code == ArchiveImportErrorCode.UPLOAD_TIMEOUT:
            self.status_code = 408
        elif code in ARCHIVE_CLIENT_ERROR_CODES:
            self.status_code = 400
        else:
            self.status_code = 500
        super().__init__(self.public_detail)


@dataclass(frozen=True)
class SafeArchiveEntry:
    info: zipfile.ZipInfo
    parts: tuple[str, ...]
    is_directory: bool

    @property
    def relative_path(self) -> str:
        return "/".join(self.parts)


@dataclass(frozen=True)
class ArchiveInspection:
    entries: tuple[SafeArchiveEntry, ...]
    file_count: int
    directory_count: int
    total_uncompressed_bytes: int
    total_compressed_bytes: int


def validate_archive_content_type(content_type: str | None) -> None:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    if normalized != "application/zip":
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_CONTENT_TYPE)


def decode_archive_name(encoded_name: str | None, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    if not encoded_name:
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ARCHIVE_NAME)
    try:
        decoded = unquote_to_bytes(encoded_name).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ARCHIVE_NAME) from exc

    decoded = unicodedata.normalize("NFC", decoded).strip()
    if (
        not decoded
        or len(decoded) > active_settings.max_archive_name_length
        or "/" in decoded
        or "\\" in decoded
        or "\x00" in decoded
        or any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        or WINDOWS_DRIVE_PATTERN.match(decoded)
        or ":" in decoded
        or any(unicodedata.category(character) == "Cf" for character in decoded)
        or not decoded.lower().endswith(".zip")
        or decoded[:-4].strip(" .") == ""
    ):
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ARCHIVE_NAME)
    return decoded


def generate_archive_repository_id(archive_name: str, content_digest: str) -> str:
    stem = archive_name[:-4]
    readable = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-").lower()
    if not readable:
        readable = "archive"
    readable = readable[:96].rstrip("-") or "archive"
    return f"{readable}-{content_digest[:12]}"


def _validate_entry_parts(
    info: zipfile.ZipInfo,
    settings: Settings,
) -> tuple[tuple[str, ...], bool]:
    original_name = getattr(info, "orig_filename", info.filename)
    if not isinstance(original_name, str):
        raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)
    raw_name = unicodedata.normalize("NFC", original_name)
    if (
        not raw_name
        or "\x00" in raw_name
        or "\\" in raw_name
        or raw_name.startswith("/")
        or raw_name.startswith("//")
        or WINDOWS_DRIVE_PATTERN.match(raw_name)
    ):
        raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)

    is_directory = info.is_dir() or raw_name.endswith("/")
    path_text = raw_name[:-1] if is_directory else raw_name
    if not path_text or len(path_text) > settings.max_archive_path_length:
        code = (
            ArchiveImportErrorCode.PATH_LENGTH_EXCEEDED
            if len(path_text) > settings.max_archive_path_length
            else ArchiveImportErrorCode.UNSAFE_ENTRY_PATH
        )
        raise ArchiveImportError(code)

    raw_parts = path_text.split("/")
    if any(
        not part
        or len(part) > 255
        or part in {".", ".."}
        or ":" in part
        or any(
            ord(character) < 32
            or ord(character) == 127
            or character in '<>"|?*'
            or unicodedata.category(character) == "Cf"
            for character in part
        )
        or part.rstrip(" .") != part
        or part.split(".", 1)[0].upper() in WINDOWS_DEVICE_NAMES
        for part in raw_parts
    ):
        raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)
    if len(raw_parts) > settings.max_archive_path_depth:
        raise ArchiveImportError(ArchiveImportErrorCode.PATH_DEPTH_EXCEEDED)
    return tuple(raw_parts), is_directory


def _validate_entry_type(info: zipfile.ZipInfo, is_directory: bool) -> None:
    if info.flag_bits & (0x1 | 0x40 | 0x2000):
        raise ArchiveImportError(ArchiveImportErrorCode.ENCRYPTED_ARCHIVE)
    if info.flag_bits & 0x20 or info.compress_type not in ALLOWED_COMPRESSION_METHODS:
        raise ArchiveImportError(ArchiveImportErrorCode.UNSUPPORTED_COMPRESSION)

    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(mode)
    if is_directory:
        if file_type not in {0, stat.S_IFDIR}:
            raise ArchiveImportError(ArchiveImportErrorCode.SPECIAL_FILE)
    elif file_type not in {0, stat.S_IFREG}:
        raise ArchiveImportError(ArchiveImportErrorCode.SPECIAL_FILE)


def preflight_zip_central_directory(
    archive_path: Path,
    settings: Settings | None = None,
) -> None:
    active_settings = settings or get_settings()
    try:
        archive_size = archive_path.stat().st_size
        with archive_path.open("rb") as handle:
            tail_size = min(archive_size, EOCD_MAX_SEARCH)
            handle.seek(archive_size - tail_size)
            tail = handle.read(tail_size)
    except OSError as exc:
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE) from exc

    search_end = len(tail)
    eocd_values: tuple | None = None
    eocd_offset_in_tail = -1
    while search_end >= EOCD_MIN_SIZE:
        candidate = tail.rfind(EOCD_SIGNATURE, 0, search_end)
        if candidate < 0:
            break
        if candidate + EOCD_MIN_SIZE <= len(tail):
            values = EOCD_STRUCT.unpack_from(tail, candidate)
            comment_length = values[-1]
            if candidate + EOCD_MIN_SIZE + comment_length == len(tail):
                eocd_values = values
                eocd_offset_in_tail = candidate
                break
        search_end = candidate

    if eocd_values is None:
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)

    (
        _signature,
        disk_number,
        central_directory_disk,
        entries_on_disk,
        total_entries,
        central_directory_size,
        central_directory_offset,
        _comment_length,
    ) = eocd_values
    if (
        disk_number != 0
        or central_directory_disk != 0
        or entries_on_disk != total_entries
        or total_entries == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)
    if total_entries > active_settings.max_archive_files + active_settings.max_archive_directories:
        raise ArchiveImportError(ArchiveImportErrorCode.FILE_LIMIT_EXCEEDED)
    if central_directory_size > active_settings.max_archive_central_directory_bytes:
        raise ArchiveImportError(
            ArchiveImportErrorCode.CENTRAL_DIRECTORY_LIMIT_EXCEEDED
        )

    tail_start = archive_size - len(tail)
    eocd_absolute_offset = tail_start + eocd_offset_in_tail
    if central_directory_offset + central_directory_size != eocd_absolute_offset:
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)


def inspect_zip_archive(
    archive: zipfile.ZipFile,
    settings: Settings | None = None,
    budget: ImportBudget | None = None,
) -> ArchiveInspection:
    active_settings = settings or get_settings()
    entries: list[SafeArchiveEntry] = []
    seen_entries: set[str] = set()
    file_paths: set[str] = set()
    directory_paths: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0
    file_count = 0

    archive_entries = archive.infolist()
    if len(archive_entries) > active_settings.max_archive_files + active_settings.max_archive_directories:
        raise ArchiveImportError(ArchiveImportErrorCode.FILE_LIMIT_EXCEEDED)

    for info in archive_entries:
        if budget is not None:
            budget.check_deadline()
        parts, is_directory = _validate_entry_parts(info, active_settings)
        _validate_entry_type(info, is_directory)
        normalized_path = "/".join(parts)
        normalized_key = normalized_path.casefold()
        if normalized_key in seen_entries:
            raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)
        seen_entries.add(normalized_key)

        parent_parts = parts[:-1] if not is_directory else parts
        for depth in range(1, len(parent_parts) + 1):
            directory_paths.add("/".join(parent_parts[:depth]).casefold())

        if is_directory:
            directory_paths.add(normalized_key)
        else:
            if info.file_size < 0 or info.compress_size < 0:
                raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)
            file_count += 1
            file_paths.add(normalized_key)
            total_uncompressed += info.file_size
            total_compressed += info.compress_size

            if file_count > active_settings.max_archive_files:
                raise ArchiveImportError(ArchiveImportErrorCode.FILE_LIMIT_EXCEEDED)
            if total_uncompressed > active_settings.max_archive_uncompressed_bytes:
                raise ArchiveImportError(ArchiveImportErrorCode.UNCOMPRESSED_LIMIT_EXCEEDED)
            if info.file_size:
                if info.compress_size <= 0:
                    raise ArchiveImportError(ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED)
                if info.file_size / info.compress_size > active_settings.max_archive_compression_ratio:
                    raise ArchiveImportError(ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED)

        if len(directory_paths) > active_settings.max_archive_directories:
            raise ArchiveImportError(ArchiveImportErrorCode.DIRECTORY_LIMIT_EXCEEDED)
        entries.append(SafeArchiveEntry(info=info, parts=parts, is_directory=is_directory))

    for file_path in file_paths:
        parts = file_path.split("/")
        if file_path in directory_paths:
            raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)
        if any("/".join(parts[:depth]) in file_paths for depth in range(1, len(parts))):
            raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH)

    if total_uncompressed:
        if total_compressed <= 0:
            raise ArchiveImportError(ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED)
        if total_uncompressed / total_compressed > active_settings.max_archive_compression_ratio:
            raise ArchiveImportError(ArchiveImportErrorCode.COMPRESSION_RATIO_EXCEEDED)

    return ArchiveInspection(
        entries=tuple(entries),
        file_count=file_count,
        directory_count=len(directory_paths),
        total_uncompressed_bytes=total_uncompressed,
        total_compressed_bytes=total_compressed,
    )


def _should_extract_entry(entry: SafeArchiveEntry, settings: Settings) -> bool:
    if entry.is_directory or entry.info.file_size > settings.max_file_size_bytes:
        return False
    path = Path(*entry.parts)
    if any(
        should_ignore_dir(parent) or should_ignore_dir(Path(parent.name.lower()))
        for parent in path.parents
    ):
        return False
    return not should_ignore_file(path)


def extract_safe_archive(
    archive: zipfile.ZipFile,
    inspection: ArchiveInspection,
    destination_root: Path,
    settings: Settings | None = None,
    budget: ImportBudget | None = None,
) -> None:
    active_settings = settings or get_settings()
    resolved_root = destination_root.resolve()
    extracted_bytes = 0

    for entry in inspection.entries:
        if budget is not None:
            budget.check_deadline()
        if not _should_extract_entry(entry, active_settings):
            continue

        destination = (destination_root / Path(*entry.parts)).resolve()
        try:
            destination.relative_to(resolved_root)
        except ValueError as exc:
            raise ArchiveImportError(ArchiveImportErrorCode.UNSAFE_ENTRY_PATH) from exc

        destination.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with archive.open(entry.info, "r") as source, destination.open("xb") as target:
                while True:
                    if budget is not None:
                        budget.check_deadline()
                    chunk = source.read(COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    extracted_bytes += len(chunk)
                    if (
                        written > entry.info.file_size
                        or extracted_bytes > active_settings.max_archive_uncompressed_bytes
                    ):
                        raise ArchiveImportError(
                            ArchiveImportErrorCode.UNCOMPRESSED_LIMIT_EXCEEDED
                        )
                    target.write(chunk)
        except ArchiveImportError:
            raise
        except (RuntimeError, zipfile.BadZipFile, EOFError) as exc:
            raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP) from exc
        if written != entry.info.file_size:
            raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)


def _prune_unparsed_files(snapshot_root: Path, parsed_files: list[ParsedFile]) -> None:
    retained = {item.file_path for item in parsed_files}
    for path in sorted(snapshot_root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            relative = path.relative_to(snapshot_root).as_posix()
            if relative not in retained:
                path.unlink()
        elif path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass


def _write_source_manifest(
    repository_dir: Path,
    manifest: dict,
) -> None:
    manifest_dir = repository_dir / ".codebase_agent"
    if _is_link_or_junction(manifest_dir):
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    destination = manifest_dir / SOURCE_MANIFEST_FILE
    if destination.is_symlink():
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE)
    temporary = manifest_dir / f".{SOURCE_MANIFEST_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(callable(is_junction) and is_junction())


def _commit_snapshot_and_manifest(
    repository_id: str,
    staging_root: Path,
    manifest: dict,
    settings: Settings,
) -> None:
    repository_root = settings.repos_dir.resolve()
    repository_dir = settings.repos_dir / repository_id
    if (
        _is_link_or_junction(repository_dir)
        or repository_dir.resolve().parent != repository_root
        or _is_link_or_junction(staging_root)
        or staging_root.resolve().parent != repository_root
    ):
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE)
    repository_dir.mkdir(parents=True, exist_ok=True)
    snapshot_dir = repository_dir / "source_snapshot"
    backup_dir = repository_dir / f".source_snapshot.backup-{uuid.uuid4().hex}"
    had_snapshot = snapshot_dir.exists()

    try:
        if had_snapshot:
            if not snapshot_dir.is_dir() or _is_link_or_junction(snapshot_dir):
                raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE)
            snapshot_dir.replace(backup_dir)
        try:
            staging_root.replace(snapshot_dir)
            _write_source_manifest(repository_dir, manifest)
        except Exception:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
            if had_snapshot and backup_dir.exists():
                backup_dir.replace(snapshot_dir)
            raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


def load_zip_repository(
    archive_path: Path,
    archive_name: str,
    content_digest: str,
    *,
    upload_size: int | None = None,
    budget: ImportBudget | None = None,
) -> tuple[str, list[DocumentChunk], int]:
    settings = get_settings()
    if budget is not None:
        budget.check_deadline()
    try:
        archive_size = archive_path.stat().st_size
    except OSError as exc:
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE) from exc
    if upload_size is not None and archive_size != upload_size:
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE)
    if archive_size > settings.max_archive_upload_bytes:
        raise ArchiveImportError(ArchiveImportErrorCode.UPLOAD_TOO_LARGE)
    if archive_size <= 0:
        raise ArchiveImportError(ArchiveImportErrorCode.EMPTY_UPLOAD)
    preflight_zip_central_directory(archive_path, settings)
    if not zipfile.is_zipfile(archive_path):
        raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP)

    repository_id = generate_archive_repository_id(archive_name, content_digest)
    staging_path = Path(
        tempfile.mkdtemp(
            prefix=f".{repository_id}-zip-staging-",
            dir=settings.repos_dir,
        )
    )
    try:
        try:
            with zipfile.ZipFile(archive_path, mode="r") as archive:
                inspection = inspect_zip_archive(archive, settings=settings, budget=budget)
                extract_safe_archive(
                    archive,
                    inspection,
                    staging_path,
                    settings=settings,
                    budget=budget,
                )
        except ArchiveImportError:
            raise
        except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
            raise ArchiveImportError(ArchiveImportErrorCode.INVALID_ZIP) from exc

        if budget is not None:
            budget.check_deadline()
        parsed_files = read_repository_files(staging_path)
        if not parsed_files:
            raise ArchiveImportError(ArchiveImportErrorCode.NO_SUPPORTED_FILES)
        _prune_unparsed_files(staging_path, parsed_files)
        if budget is not None:
            budget.check_deadline()
        chunks = split_files_into_chunks(parsed_files, repository_id)
        if budget is not None:
            budget.check_deadline()

        snapshot_bytes = sum(
            (staging_path / Path(*Path(item.file_path).parts)).stat().st_size
            for item in parsed_files
        )
        manifest = {
            "manifest_version": SOURCE_MANIFEST_VERSION,
            "source": ARCHIVE_SOURCE,
            "source_type": ARCHIVE_SOURCE,
            "source_name": archive_name,
            "display_name": archive_name,
            "upload_name": archive_name,
            "source_digest": content_digest,
            "created_at": int(time.time()),
            "upload_bytes": archive_size,
            "archive_files": inspection.file_count,
            "archive_directories": inspection.directory_count,
            "total_uncompressed_bytes": inspection.total_uncompressed_bytes,
            "snapshot_bytes": snapshot_bytes,
            "files_indexed": len(parsed_files),
            "file_paths": [item.file_path for item in parsed_files],
        }
        _commit_snapshot_and_manifest(
            repository_id,
            staging_path,
            manifest,
            settings,
        )
        return repository_id, chunks, len(parsed_files)
    except ArchiveImportError:
        raise
    except Exception as exc:
        raise ArchiveImportError(ArchiveImportErrorCode.STORAGE_FAILURE) from exc
    finally:
        shutil.rmtree(staging_path, ignore_errors=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
