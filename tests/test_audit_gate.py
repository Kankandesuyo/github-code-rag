import ast
import importlib.util
from datetime import date
from pathlib import Path

import pytest


REMOTE_CHROMA_CLIENTS = frozenset({"HttpClient", "AsyncHttpClient", "CloudClient"})
REMOTE_CHROMA_STRINGS = (
    "/api/v2",
    "chroma run",
    "chromadb.server",
    "chromadb.api.fastapi",
)


def load_gate():
    path = Path("scripts/run_pip_audit.py")
    assert path.exists(), "audit gate script is missing"
    spec = importlib.util.spec_from_file_location("run_pip_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def find_chroma_remote_surfaces(source: str) -> list[str]:
    tree = ast.parse(source)
    chroma_module_aliases: set[str] = set()
    remote_client_aliases: set[str] = set()
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".")[0]
                if imported.name == "chromadb":
                    chroma_module_aliases.add(local_name)
                if imported.name.startswith("chromadb.server"):
                    findings.append(f"server import: {imported.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("chromadb.server"):
                findings.append(f"server import: {module}")
            if module == "chromadb" or module.startswith("chromadb."):
                for imported in node.names:
                    if imported.name in REMOTE_CHROMA_CLIENTS:
                        remote_client_aliases.add(imported.asname or imported.name)
                        findings.append(f"remote client import: {module}.{imported.name}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            referenced_parts = _dotted_name(node).split(".")
            if (
                len(referenced_parts) >= 2
                and referenced_parts[0] in chroma_module_aliases
                and referenced_parts[-1] in REMOTE_CHROMA_CLIENTS
            ):
                findings.append(f"remote client reference: {'.'.join(referenced_parts)}")
        if not isinstance(node, ast.Call):
            continue
        called = _dotted_name(node.func)
        called_parts = called.split(".")
        if called in remote_client_aliases:
            findings.append(f"remote client call: {called}")
        elif (
            len(called_parts) >= 2
            and called_parts[0] in chroma_module_aliases
            and called_parts[-1] in REMOTE_CHROMA_CLIENTS
        ):
            findings.append(f"remote client call: {called}")

        string_values = [
            item.value.lower()
            for item in ast.walk(node)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        ]
        joined_values = " ".join(string_values)
        for forbidden in REMOTE_CHROMA_STRINGS:
            if forbidden in joined_values:
                findings.append(f"remote server launch: {forbidden}")

    return findings


def exercise_chroma_runtime(*, tmp_path: Path, monkeypatch):
    from app.config import get_settings
    from app.services import embedding_service, vector_store

    calls: dict[str, object] = {
        "persistent": [],
        "remote": [],
        "collections": [],
    }

    class FakePersistentClient:
        def get_or_create_collection(self, **kwargs):
            calls["collections"].append(kwargs)
            return object()

        def delete_collection(self, _name):
            return None

    persistent_result = FakePersistentClient()
    calls["persistent_result"] = persistent_result

    def persistent_client(**kwargs):
        calls["persistent"].append(kwargs)
        return persistent_result

    def remote_client(*args, **kwargs):
        calls["remote"].append((args, kwargs))
        raise AssertionError("remote Chroma client must not be constructed")

    settings = get_settings()
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    monkeypatch.setattr(vector_store.chromadb, "PersistentClient", persistent_client)
    for client_name in REMOTE_CHROMA_CLIENTS:
        if hasattr(vector_store.chromadb, client_name):
            monkeypatch.setattr(vector_store.chromadb, client_name, remote_client)

    vector_store.get_chroma_client.cache_clear()
    embedding_service.get_embedding_function.cache_clear()
    try:
        client = vector_store.get_chroma_client()
        vector_store.get_or_create_collection("audit-gate")
        vector_store.recreate_collection("audit-gate")
        calls["chroma_settings"] = calls["persistent"][0]["settings"]
        return client, calls
    finally:
        vector_store.get_chroma_client.cache_clear()
        embedding_service.get_embedding_function.cache_clear()


def test_audit_gate_has_only_reviewed_future_dated_waivers():
    run_pip_audit = load_gate()
    assert run_pip_audit.ALLOWED_WAIVERS == frozenset(
        {
            "PYSEC-2026-311",
            "CVE-2026-45830",
            "CVE-2026-45831",
            "CVE-2026-45833",
        }
    )
    assert run_pip_audit.WAIVER_EXPIRES_ON == date(2026, 10, 1)
    assert run_pip_audit.WAIVER_EXPIRES_ON > date(2026, 9, 1)


def test_audit_gate_applies_waiver_only_before_expiry():
    run_pip_audit = load_gate()
    active = run_pip_audit.build_audit_command(today=date(2026, 9, 30))
    expired = run_pip_audit.build_audit_command(today=date(2026, 10, 1))

    for advisory in run_pip_audit.ALLOWED_WAIVERS:
        advisory_index = active.index(advisory)
        assert active[advisory_index - 1] == "--ignore-vuln"
    assert "--ignore-vuln" not in expired


def test_audit_gate_propagates_runner_exit_code():
    run_pip_audit = load_gate()
    calls: list[list[str]] = []

    def runner(command: list[str]) -> int:
        calls.append(command)
        return 7

    result = run_pip_audit.run_audit(today=date(2026, 9, 30), runner=runner)

    assert result == 7
    assert calls[0].count("--ignore-vuln") == 4
    assert set(calls[0][calls[0].index("--ignore-vuln") + 1 :: 2]) == run_pip_audit.ALLOWED_WAIVERS


def test_chroma_advisory_attack_surfaces_are_not_exposed():
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in Path("app").rglob("*.py")
    )

    assert "chromadb.HttpClient" not in source
    assert "chromadb.server.fastapi" not in source
    assert "/api/v2" not in source


def test_all_chroma_collection_creation_supplies_owned_embedding_function():
    tree = ast.parse(Path("app/services/vector_store.py").read_text(encoding="utf-8"))
    collection_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"create_collection", "get_or_create_collection", "get_collection"}:
            collection_calls.append(node)

    assert collection_calls
    for call in collection_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "embedding_function" in keywords
        assert isinstance(keywords["embedding_function"], ast.Call)
        assert getattr(keywords["embedding_function"].func, "id", None) == "get_embedding_function"


def test_chroma_waiver_rationale_and_removal_conditions_are_documented():
    documents = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("README.md", "SECURITY_LOG.md", "UPDATE_README.md")
    )

    for required_text in (
        "CVE-2026-45829",
        "CVE-2026-45830",
        "CVE-2026-45831",
        "CVE-2026-45833",
        "CVSS 9.3",
        "2026-10-01",
        "PersistentClient",
        "HttpClient",
        "embedding_function",
        "https://github.com/advisories/GHSA-f4j7-r4q5-qw2c",
        "移除豁免",
    ):
        assert required_text in documents


@pytest.mark.parametrize(
    "source",
    (
        "import chromadb as c\nc.HttpClient(host='remote')",
        "from chromadb import HttpClient as Remote\nRemote(host='remote')",
        "from chromadb import AsyncHttpClient\nAsyncHttpClient(host='remote')",
        "from chromadb import CloudClient as Cloud\nCloud()",
        "import chromadb.server.fastapi as server\nserver.FastAPI()",
        "from chromadb.server.fastapi import FastAPI as ChromaServer\nChromaServer()",
        "import subprocess\nsubprocess.run(['chroma', 'run'])",
        "import uvicorn\nuvicorn.run('chromadb.server.fastapi:app')",
    ),
)
def test_remote_surface_detector_catches_aliases_imports_and_launches(source):
    assert find_chroma_remote_surfaces(source)


@pytest.mark.parametrize(
    "source",
    (
        "from chromadb import HttpClient as Remote",
        "from chromadb import AsyncHttpClient",
        "from chromadb import CloudClient as Cloud",
    ),
)
def test_remote_surface_detector_rejects_remote_client_import_without_call(source):
    assert find_chroma_remote_surfaces(source)


def test_remote_surface_detector_rejects_aliased_remote_client_reference():
    source = "import chromadb as c\nremote_factory = c.HttpClient"

    assert find_chroma_remote_surfaces(source)


def test_remote_surface_detector_allows_local_persistent_client():
    source = "import chromadb as c\nc.PersistentClient(path='chroma_db')"

    assert find_chroma_remote_surfaces(source) == []


def test_application_ast_has_no_chroma_remote_or_server_surface():
    violations = {
        str(path): find_chroma_remote_surfaces(path.read_text(encoding="utf-8"))
        for path in Path("app").rglob("*.py")
    }

    assert {path: findings for path, findings in violations.items() if findings} == {}


def test_delivery_commands_do_not_launch_chroma_server():
    paths = [Path("Dockerfile"), Path("compose.yaml")]
    paths.extend(Path(".github/workflows").glob("*.yml"))
    paths.extend(Path(".github/workflows").glob("*.yaml"))
    contents = "\n".join(path.read_text(encoding="utf-8").lower() for path in paths)

    assert "chroma run" not in contents
    assert "chromadb.server" not in contents
    assert "/api/v2" not in contents


def test_settings_expose_only_local_chroma_configuration():
    from app.config import Settings

    chroma_fields = {name for name in Settings.model_fields if name.startswith("chroma_")}

    assert chroma_fields == {"chroma_dir", "chroma_anonymized_telemetry"}


def test_runtime_constructs_only_persistent_client_in_temporary_directory(
    tmp_path, monkeypatch
):
    client, calls = exercise_chroma_runtime(tmp_path=tmp_path, monkeypatch=monkeypatch)

    assert client is calls["persistent_result"]
    assert calls["persistent"] == [
        {"path": str(tmp_path / "chroma"), "settings": calls["chroma_settings"]}
    ]
    assert calls["remote"] == []


def test_runtime_collection_calls_receive_owned_hash_embedding(tmp_path, monkeypatch):
    _, calls = exercise_chroma_runtime(tmp_path=tmp_path, monkeypatch=monkeypatch)

    from app.services.embedding_service import HashEmbeddingFunction

    assert len(calls["collections"]) == 2
    for kwargs in calls["collections"]:
        assert isinstance(kwargs["embedding_function"], HashEmbeddingFunction)
        assert kwargs["embedding_function"].name() == "github_code_rag_hash"
