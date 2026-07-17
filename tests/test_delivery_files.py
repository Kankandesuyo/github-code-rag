import re
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
    assert (
        '- "127.0.0.1:8000:8000"' in compose
        or "- '127.0.0.1:8000:8000'" in compose
        or "- 127.0.0.1:8000:8000" in compose
    )
    assert '- "8000:8000"' not in compose
    assert "- '8000:8000'" not in compose
    assert "- 8000:8000" not in compose
    assert "env_file:" in compose


def test_environment_example_documents_production_security_boundary():
    env_example = read(".env.example")

    for setting in (
        "DEPLOYMENT_MODE",
        "ALLOWED_HOSTS",
        "PUBLIC_BASE_URL",
        "TLS_TERMINATED_BY_PROXY",
    ):
        assert f"{setting}=" in env_example


def test_ci_uses_python_312_and_runs_required_checks():
    workflow = read(".github/workflows/ci.yml")

    assert 'python-version: "3.12"' in workflow
    assert "python -m compileall -q app tests" in workflow
    assert "python -m pip check" in workflow
    assert "python -m pytest tests -q" in workflow


def test_default_pytest_command_does_not_scan_imported_repositories():
    pytest_config = read("pytest.ini")

    assert "testpaths = tests" in pytest_config
    assert "repos" in pytest_config
    assert "chroma_db" in pytest_config
    assert "dist" in pytest_config


def test_ci_runs_expiring_dependency_audit_gate_without_inline_waivers():
    workflow = read(".github/workflows/ci.yml")
    gate = read("scripts/run_pip_audit.py")

    assert "python -m pip install pip-audit" in workflow
    assert "python scripts/run_pip_audit.py" in workflow
    assert "--ignore-vuln" not in workflow
    assert "continue-on-error: true" not in workflow
    assert gate.count('"PYSEC-2026-311"') == 1
    assert "CVE-2026-45829" not in gate


def test_docker_base_image_is_digest_pinned():
    dockerfile = read("Dockerfile")

    assert re.search(
        r"^FROM python:3\.12-slim@sha256:[0-9a-f]{64}$",
        dockerfile,
        flags=re.MULTILINE,
    )
    assert "USER appuser" in dockerfile


def test_compose_drops_privileges_and_bounds_resources():
    compose = read("compose.yaml")

    assert "no-new-privileges:true" in compose
    assert "cap_drop:" in compose
    assert "- ALL" in compose
    assert "cpus:" in compose
    assert "mem_limit:" in compose
    assert "127.0.0.1:8000:8000" in compose


def test_security_delivery_boundaries_are_documented():
    documents = "\n".join(
        read(path) for path in ("README.md", "SECURITY_LOG.md", "UPDATE_README.md")
    )

    for required_text in (
        "本地威胁模型",
        "生产威胁模型",
        "ALLOWED_HOSTS",
        "PUBLIC_BASE_URL",
        "TLS_TERMINATED_BY_PROXY",
        "client_max_body_size",
        "security_audit.jsonl",
        "MAX_REPOSITORY_REQUESTS",
        "MAX_CONCURRENT_IMPORTS",
        "共享限流",
        "共享队列",
    ):
        assert required_text in documents
