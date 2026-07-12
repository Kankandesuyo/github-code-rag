from pathlib import Path


ALLOWED_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".cs",
    ".php",
    ".rb",
    ".kt",
    ".kts",
    ".swift",
    ".vue",
    ".svelte",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".md",
    ".mdx",
    ".rst",
    ".adoc",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".xml",
    ".sql",
    ".graphql",
    ".gql",
    ".proto",
    ".prisma",
    ".gradle",
    ".csv",
    ".tsv",
    ".txt",
    ".pdf",
    ".docx",
    ".xlsx",
}

ALLOWED_FILE_NAMES = {
    "dockerfile",
    "containerfile",
    "makefile",
    "cmakelists.txt",
    "requirements.txt",
    "readme",
    "license",
    "notice",
    "changelog",
    "contributing",
    "pipfile",
    "gemfile",
    "rakefile",
    "cargo.toml",
    "go.mod",
    "go.sum",
    "package.json",
    "pyproject.toml",
    "setup.py",
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
    ".codebase_agent",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    "coverage",
    "target",
    "vendor",
}

IGNORED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets.json",
    "package-lock.json",
    "yarn.lock",
}

IGNORED_FILE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".mp3",
    ".wav",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".jar",
    ".pyc",
    ".pem",
    ".key",
    ".crt",
    ".cer",
    ".cert",
    ".p12",
    ".pfx",
    ".jks",
    ".keystore",
}

SENSITIVE_FILE_PATTERNS = (
    ".env.",
    "secret",
    "secrets",
    "credential",
    "credentials",
    "private-key",
    "private_key",
)


def should_ignore_dir(path: Path) -> bool:
    return path.name in IGNORED_DIRS


def should_ignore_file(path: Path) -> bool:
    name = path.name
    normalized_name = name.lower()
    suffix = path.suffix.lower()
    if normalized_name in IGNORED_FILE_NAMES:
        return True
    if any(pattern in normalized_name for pattern in SENSITIVE_FILE_PATTERNS):
        return True
    if suffix in IGNORED_FILE_EXTENSIONS:
        return True
    if normalized_name in ALLOWED_FILE_NAMES:
        return False
    return suffix not in ALLOWED_EXTENSIONS


def safe_relative_path(path: Path, root: Path) -> str:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not resolved_path.is_relative_to(resolved_root):
        raise ValueError("path is outside repository root")
    return resolved_path.relative_to(resolved_root).as_posix()
