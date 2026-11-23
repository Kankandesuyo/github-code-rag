import json
import re
from threading import Barrier
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import llm_service, repo_loader, vector_store
from app.services.embedding_service import get_embedding_function, get_embedding_signature
from app.services.file_parser import ParsedFile, split_files_into_chunks
from app.services.manifest_service import RepositoryManifestCache
from app.utils import atomic_files


@pytest.fixture
def hash_store(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "repos_dir", tmp_path / "repos")
    monkeypatch.setattr(settings, "chroma_dir", tmp_path / "chroma")
    monkeypatch.setattr(settings, "embedding_provider", "hash")
    vector_store.close_chroma_client()
    get_embedding_function.cache_clear()
    yield settings
    vector_store.close_chroma_client()
    get_embedding_function.cache_clear()


def test_collection_name_preserves_embedding_signature_for_long_ids(hash_store, monkeypatch):
    repository_id = "r" * 139 + "a"
    name = vector_store.get_collection_name(repository_id)
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_]{1,61}[A-Za-z0-9]", name)
    assert name.endswith(get_embedding_signature())
    assert vector_store.get_collection_name("r" * 139 + "b") != name
    monkeypatch.setattr(hash_store, "hash_embedding_dimensions", 128)
    assert vector_store.get_collection_name(repository_id) != name


def test_legacy_collection_can_be_read_then_rebuilt_on_import(hash_store):
    repository_id = "demo"
    chunks = split_files_into_chunks([ParsedFile("README.md", "# Demo health endpoint")], repository_id)
    old_manifest = vector_store.build_vector_index_manifest(repository_id, chunks)
    legacy_name = f"repo_demo_{get_embedding_signature()}"
    old_manifest["collection_name"] = legacy_name
    legacy = vector_store.get_chroma_client().create_collection(
        legacy_name, metadata={"repository_id": repository_id}, embedding_function=get_embedding_function()
    )
    vector_store.add_chunks_to_collection(legacy, repository_id, chunks)
    vector_store.save_vector_index_manifest(repository_id, old_manifest)
    assert vector_store.retrieve_relevant_chunks(repository_id, "health")[0]["metadata"]["file_path"] == "README.md"

    result = vector_store.index_chunks_incremental(repository_id, chunks)
    assert result.chunks_written == len(chunks)
    assert not result.index_cached
    manifest = vector_store.load_vector_index_manifest(repository_id)
    assert manifest["collection_name"] == vector_store.get_collection_name(repository_id)
    assert vector_store.get_repository_collection(repository_id).count() == len(chunks)
    assert vector_store.index_chunks_incremental(repository_id, chunks).index_cached


@pytest.mark.parametrize("kind", ["analysis", "vector"])
def test_manifest_replace_failure_preserves_previous_json(tmp_path, monkeypatch, kind):
    monkeypatch.setattr(get_settings(), "repos_dir", tmp_path)
    cache = RepositoryManifestCache()
    repo = tmp_path / "demo"
    if kind == "analysis":
        path = cache.manifest_path(repo)

        def save(value):
            cache.save(repo, {}, {"value": value})
    else:
        path = vector_store.vector_index_manifest_path("demo")

        def save(value):
            vector_store.save_vector_index_manifest("demo", {"value": value})

    save("before")
    before = path.read_bytes()
    real_replace = atomic_files.os.replace

    def interrupted_replace(source, destination):
        if destination == path:
            assert source.parent == path.parent
            assert "after" in source.read_text(encoding="utf-8")
            assert path.read_bytes() == before
            raise OSError("simulated interrupted replacement")
        return real_replace(source, destination)

    monkeypatch.setattr(atomic_files.os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="interrupted replacement"):
        save("after")
    assert path.read_bytes() == before
    assert list(path.parent.iterdir()) == [path]


def test_llm_client_uses_configured_timeout_and_bounded_retries(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "llm_timeout_seconds", 17)
    monkeypatch.setattr(llm_service, "OpenAI", lambda **kwargs: kwargs)
    assert llm_service.get_llm_client() == {
        "api_key": "test-key",
        "base_url": settings.deepseek_base_url,
        "timeout": 17,
        "max_retries": 1,
    }


@pytest.mark.parametrize("hyde_fails", [False, True])
def test_rewrite_and_hyde_overlap_and_parse_fenced_json(monkeypatch, hyde_fails):
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "enable_query_expansion", True)
    monkeypatch.setattr(settings, "enable_hyde", True)
    rendezvous = Barrier(2, timeout=3)

    def create(**kwargs):
        rendezvous.wait()
        is_hyde = "HyDE" in kwargs["messages"][0]["content"]
        if is_hyde and hyde_fails:
            raise RuntimeError("HyDE unavailable")
        fence = chr(96) * 3
        content = "hypothetical evidence" if is_hyde else f'{fence}json\n["rewritten", "question"]\n{fence}'
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm_service, "get_llm_client", lambda: client)
    expected = ["question", "rewritten"]
    if not hyde_fails:
        expected.append("hypothetical evidence")
    assert llm_service.build_retrieval_queries("question") == expected


def test_github_import_indexes_the_resolved_commit_and_records_it(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "repos_dir", tmp_path)
    sha = "ad" * 20
    blob = {"type": "blob", "path": "README.md", "size": 8}

    def fetch_json(url, _timeout, **_kwargs):
        if url == "https://api.github.com/repos/example/demo":
            return {"default_branch": "main"}
        if url.endswith("/commits/main"):
            return {"sha": sha}
        assert url.endswith(f"/git/trees/{sha}?recursive=1")
        return {"tree": [blob]}

    monkeypatch.setattr(repo_loader, "fetch_github_json", fetch_json)
    monkeypatch.setattr(repo_loader, "fetch_remote_file", lambda *_: ParsedFile("README.md", "# Demo"))
    repository_id, _, _ = repo_loader.load_repository("https://github.com/example/demo")
    manifest_path = tmp_path / repository_id / ".codebase_agent" / "remote_repository_manifest.json"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["commit_sha"] == sha


def test_replacing_collection_reports_unexpected_delete_error(monkeypatch, caplog):
    def fail_delete(_collection_name):
        raise OSError("private database path")

    def unexpected_create(**_kwargs):
        pytest.fail("replacement must stop after an unexpected delete failure")

    client = SimpleNamespace(delete_collection=fail_delete, get_or_create_collection=unexpected_create)
    monkeypatch.setattr(vector_store, "get_chroma_client", lambda: client)
    with pytest.raises(vector_store.VectorStoreError, match="failed to replace repository vector collection"):
        vector_store.recreate_collection("demo")
    assert "private database path" not in caplog.text
