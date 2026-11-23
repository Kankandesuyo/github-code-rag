import pytest

from app.config import get_settings
from app.services import llm_service


class _Client:
    def __init__(self, calls):
        self.calls = calls
        self.chat = self
        self.completions = self

    def create(self, *, messages, **_kwargs):
        self.calls.append("hyde" if "HyDE" in messages[0]["content"] else "rewrite")

        class _Message:
            content = '["q"]'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


@pytest.fixture
def llm_calls(monkeypatch):
    calls: list[str] = []
    settings = get_settings()
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    monkeypatch.setattr(settings, "enable_query_expansion", True)
    monkeypatch.setattr(settings, "enable_hyde", True)
    monkeypatch.setattr(llm_service, "get_llm_client", lambda **_: _Client(calls))
    return calls


def test_build_retrieval_queries_skips_hyde_when_disabled(llm_calls):
    llm_service.build_retrieval_queries("怎么启动", use_hyde=False)
    assert llm_calls == ["rewrite"]


def test_build_retrieval_queries_uses_hyde_by_default(llm_calls):
    llm_service.build_retrieval_queries("怎么启动")
    assert sorted(llm_calls) == ["hyde", "rewrite"]


@pytest.mark.parametrize(("history", "followup_hyde", "expected"), [
    ([], False, True),
    ([{"role": "user", "content": "上一问"}], False, False),
    ([{"role": "user", "content": "上一问"}], True, True),
])
def test_chat_passes_hyde_flag_for_follow_ups(monkeypatch, history, followup_hyde, expected):
    from app import main
    from app.schemas import ChatRequest

    seen = {}
    monkeypatch.setattr(get_settings(), "enable_hyde_for_followups", followup_hyde)
    monkeypatch.setattr(main, "condense_question", lambda question, _history: question)

    def fake_search(_repository_id, _query, *, use_hyde=True):
        seen["use_hyde"] = use_hyde
        return [], []

    monkeypatch.setattr(main.rag_agent, "search", fake_search)
    main.retrieve_chat_evidence(ChatRequest(repository_id="demo-repo", question="它在哪", history=history))
    assert seen["use_hyde"] is expected
