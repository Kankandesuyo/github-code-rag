from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_docker_image_runs_as_non_root_with_healthcheck():
    dockerfile = read("Dockerfile")

    assert "FROM python:3.12-slim" in dockerfile
    assert "USER appuser" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert "uvicorn" in dockerfile


def test_docker_context_excludes_secrets_and_runtime_data():
    dockerignore = read(".dockerignore")

    for entry in (".env", ".git", ".venv", "repos", "chroma_db", "*.log"):
        assert entry in dockerignore


def test_compose_persists_repository_and_vector_data():
    compose = read("compose.yaml")

    assert "repos:/app/repos" in compose
    assert "chroma_db:/app/chroma_db" in compose
    assert "8000:8000" in compose
    assert "env_file:" in compose


def test_ci_uses_python_312_and_runs_required_checks():
    workflow = read(".github/workflows/ci.yml")

    assert 'python-version: "3.12"' in workflow
    assert "python -m compileall -q app tests" in workflow
    assert "python -m pip check" in workflow
    assert "python -m pytest tests -q" in workflow
