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
    dev_requirements = read("requirements-dev.txt")

    assert 'python-version: "3.12"' in workflow
    assert "python -m compileall -q app tests" in workflow
    assert "python -m pip check" in workflow
    assert "python -m ruff check --select E9,F63,F7,F82 app tests scripts" in workflow
    assert "--cov=app" in workflow
    assert "--cov-fail-under=80" in workflow
    assert "python -m pip install -r requirements-dev.txt" in workflow
    assert "pytest-cov==" in dev_requirements
    assert "ruff==" in dev_requirements


def test_github_actions_are_pinned_to_immutable_commits():
    workflows = "\n".join(
        read(path) for path in (".github/workflows/ci.yml", ".github/workflows/pages.yml")
    )
    action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", workflows)

    assert action_refs
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs)


def test_static_demo_keeps_user_content_safe_and_sources_traceable():
    demo = read("demo/index.html")

    assert "p.textContent=text" in demo
    assert 'maxlength="500"' in demo
    assert 'rel="noopener noreferrer"' in demo
    assert "load_repository_ephemeral" in demo
    assert "app/services/online_search.py#L89-L124" in demo
    assert "<script src=" not in demo


def test_default_pytest_command_does_not_scan_imported_repositories():
    pytest_config = read("pytest.ini")

    assert "testpaths = tests" in pytest_config
    assert "repos" in pytest_config
    assert "chroma_db" in pytest_config
    assert "dist" in pytest_config


def test_ci_runs_expiring_dependency_audit_gate_without_inline_waivers():
    workflow = read(".github/workflows/ci.yml")
    gate = read("scripts/run_pip_audit.py")

    assert "pip-audit==" in read("requirements-dev.txt")
    assert "python scripts/run_pip_audit.py" in workflow
    assert "--ignore-vuln" not in workflow
    assert "continue-on-error: true" not in workflow
    for advisory in (
        "PYSEC-2026-311",
        "CVE-2026-45830",
        "CVE-2026-45831",
        "CVE-2026-45833",
    ):
        assert gate.count(f'"{advisory}"') == 1
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
