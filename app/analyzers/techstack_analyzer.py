import json
import re
from pathlib import Path

from app.schemas.report_schema import TechStackResult


STACK_RULES = {
    "backend": {
        "Python": ["python"],
        "FastAPI": ["fastapi"],
        "Django": ["django"],
        "Flask": ["flask"],
        "Express": ["express"],
        "Node.js": ["node", "nodejs"],
        "Celery": ["celery"],
    },
    "frontend": {
        "React": ["react"],
        "Vue": ["vue", "@vue/"],
        "Next.js": ["next"],
        "Vite": ["vite"],
        "Svelte": ["svelte"],
        "TypeScript": ["typescript"],
    },
    "database": {
        "PostgreSQL": ["postgresql", "postgres", "psycopg", "asyncpg", "pg"],
        "MySQL": ["mysql", "pymysql", "mysqlclient"],
        "Redis": ["redis"],
        "SQLite": ["sqlite"],
        "MongoDB": ["mongodb", "mongoose", "pymongo"],
        "Prisma": ["prisma"],
        "SQLAlchemy": ["sqlalchemy"],
        "Django ORM": ["django.db", "models.model"],
    },
    "devops": {
        "Docker": ["dockerfile", "containerfile", "docker-compose", "compose.yml", "compose.yaml"],
        "GitHub Actions": [".github/workflows"],
        "Nginx": ["nginx"],
        "Gunicorn": ["gunicorn"],
        "Uvicorn": ["uvicorn"],
    },
    "ai": {
        "LangChain": ["langchain"],
        "LangGraph": ["langgraph"],
        "OpenAI SDK": ["openai"],
        "Chroma": ["chromadb", "chroma"],
        "SentenceTransformers": ["sentence-transformers", "sentence_transformers"],
        "Transformers": ["transformers"],
    },
}


class TechStackAnalyzer:
    signal_files = (
        "requirements.txt",
        "pyproject.toml",
        "package.json",
        "Dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "Pipfile",
        "go.mod",
        "Cargo.toml",
        "README.md",
    )

    def read_if_exists(self, repo_path: Path, relative_path: str) -> str:
        path = repo_path / relative_path
        if not path.exists() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def collect_signal_text(self, repo_path: Path) -> str:
        signal_parts: list[str] = []
        for file_name in self.signal_files:
            content = self.read_if_exists(repo_path, file_name)
            if content:
                signal_parts.append(f"# {file_name}\n{content}")

        package_json = self.read_if_exists(repo_path, "package.json")
        if package_json:
            try:
                parsed = json.loads(package_json)
                deps = {
                    **parsed.get("dependencies", {}),
                    **parsed.get("devDependencies", {}),
                }
                signal_parts.append(" ".join(deps.keys()))
            except json.JSONDecodeError:
                pass

        workflow_dir = repo_path / ".github" / "workflows"
        if workflow_dir.exists():
            signal_parts.append(".github/workflows")

        return "\n".join(signal_parts).lower()

    def analyze_path(self, repo_path: Path) -> TechStackResult:
        signal_text = self.collect_signal_text(repo_path)
        detected: dict[str, list[str]] = {
            "backend": [],
            "frontend": [],
            "database": [],
            "devops": [],
            "ai": [],
        }
        for category, rules in STACK_RULES.items():
            for label, needles in rules.items():
                if any(re.search(rf"(^|[^a-z0-9_\-]){re.escape(needle)}([^a-z0-9_\-]|$)", signal_text) for needle in needles):
                    detected[category].append(label)

        if "python" not in detected["backend"] and re.search(r"\.py\b|python", signal_text):
            detected["backend"].append("Python")

        return TechStackResult(**{key: sorted(set(value)) for key, value in detected.items()})
