from pathlib import Path


ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".txt",
}

IGNORED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".cache",
}

IGNORED_FILE_NAMES = {
    ".env",
    "package-lock.json",
    "yarn.lock",
}

IGNORED_FILE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".mp4",
    ".zip",
}


def should_ignore_dir(path: Path) -> bool:
    return path.name in IGNORED_DIRS


def should_ignore_file(path: Path) -> bool:
    name = path.name
    suffix = path.suffix.lower()
    if name in IGNORED_FILE_NAMES:
        return True
    if suffix in IGNORED_FILE_EXTENSIONS:
        return True
    return suffix not in ALLOWED_EXTENSIONS


def safe_relative_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
