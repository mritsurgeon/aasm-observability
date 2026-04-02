"""
SDK unit tests — no real network calls, no framework installs required.
"""
import queue
import threading
import types
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(endpoint="http://testserver"):
    from arsp_sdk._client import EventClient
    return EventClient(endpoint=endpoint, agent_id="test-agent")


# ── _context ──────────────────────────────────────────────────────────────────

def test_context_defaults():
    from arsp_sdk._context import get_agent_id, get_session_id
    # In a fresh thread, both should be None
    result = {}
    def run():
        result["a"] = get_agent_id()
        result["s"] = get_session_id()
    t = threading.Thread(target=run)
    t.start(); t.join()
    assert result["a"] is None
    assert result["s"] is None


def test_context_set_get():
    from arsp_sdk._context import set_agent_id, set_session_id, get_agent_id, get_session_id
    set_agent_id("agent-xyz")
    set_session_id("sess-abc")
    assert get_agent_id() == "agent-xyz"
    assert get_session_id() == "sess-abc"


# ── EventClient ───────────────────────────────────────────────────────────────

def test_client_send_enqueues():
    client = _make_client()
    with patch.object(client, "_flush") as mock_flush:
        client.send(type="tool_call", name="test_tool")
        # Give the worker thread a moment to drain
        import time; time.sleep(0.7)
        mock_flush.assert_called()


def test_client_send_sync_posts(requests_mock=None):
    client = _make_client()
    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(json=lambda: {"id": "evt-1"})
        result = client.send_sync(type="llm_call", name="gpt-4o", metadata={"x": 1})
        assert result == "evt-1"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "/events" in call_kwargs.args[0]


def test_client_build_payload():
    client = _make_client()
    payload = client._build("tool_call", "my_tool", {"k": "v"}, parent_id="p-1")
    assert payload["type"] == "tool_call"
    assert payload["name"] == "my_tool"
    assert payload["metadata"] == {"k": "v"}
    assert payload["parent_id"] == "p-1"
    assert "timestamp" in payload
    assert "session_id" in payload


def test_client_queue_full_drops_silently():
    from arsp_sdk._client import EventClient
    client = EventClient(endpoint="http://testserver", agent_id="a")
    # Fill the queue
    for _ in range(50_001):
        try:
            client._q.put_nowait({"x": 1})
        except queue.Full:
            break  # expected
    # Should not raise
    client.send(type="tool_call", name="overflow")


# ── init() ────────────────────────────────────────────────────────────────────

def test_init_returns_client():
    import arsp_sdk as arsp
    with patch("arsp_sdk._patches.openai_patch.patch_openai"), \
         patch("arsp_sdk._patches.langchain_patch.patch_langchain"), \
         patch("arsp_sdk._patches.crewai_patch.patch_crewai"):
        c = arsp.init(
            agent_id="test-agent-init",
            endpoint="http://localhost:9999",
            patch_openai=False,
            patch_langchain=False,
            patch_crewai=False,
        )
    from arsp_sdk._client import EventClient
    assert isinstance(c, EventClient)


def test_new_session_returns_uuid():
    import arsp_sdk as arsp
    sid = arsp.new_session()
    uuid.UUID(sid)  # raises if not valid UUID


def test_track_without_init_returns_none():
    import arsp_sdk as arsp
    arsp._client = None
    result = arsp.track("custom", "my_event")
    assert result is None


# ── OpenAI patch ──────────────────────────────────────────────────────────────

def _fake_openai_module():
    """Build a minimal stub of openai.resources.chat.completions."""
    mod = types.ModuleType("openai")
    resources = types.ModuleType("openai.resources")
    chat = types.ModuleType("openai.resources.chat")
    completions_mod = types.ModuleType("openai.resources.chat.completions")

    class FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5
        total_tokens = 15

    class FakeMessage:
        content = "hello"

    class FakeChoice:
        message = FakeMessage()

    class FakeResult:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class Completions:
        def create(self, *args, **kwargs):
            return FakeResult()

    class AsyncCompletions:
        async def create(self, *args, **kwargs):
            return FakeResult()

    completions_mod.Completions = Completions
    completions_mod.AsyncCompletions = AsyncCompletions
    return completions_mod, Completions, AsyncCompletions, FakeResult


def test_openai_patch_sync_sends_event():
    from arsp_sdk._patches.openai_patch import _wrap_sync, _extract_meta
    completions_mod, Completions, _, FakeResult = _fake_openai_module()

    client = MagicMock()
    _wrap_sync(client, Completions)

    instance = Completions()
    instance.create(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    client.send.assert_called_once()
    call_kwargs = client.send.call_args.kwargs
    assert call_kwargs["type"] == "llm_call"
    assert call_kwargs["name"] == "gpt-4o"
    assert call_kwargs["metadata"]["framework"] == "openai"


def test_openai_extract_meta_truncates():
    from arsp_sdk._patches.openai_patch import _extract_meta

    class FakeUsage:
        prompt_tokens = 1; completion_tokens = 2; total_tokens = 3

    class FakeMsg:
        content = "x" * 1000

    class FakeChoice:
        message = FakeMsg()

    class FakeResult:
        usage = FakeUsage()
        choices = [FakeChoice()]

    messages = [{"role": "user", "content": "y" * 1000}]
    meta = _extract_meta("gpt-4o", messages, FakeResult())
    assert len(meta["response"]) <= 400
    assert len(meta["messages"][0]["content"]) <= 400


# ── LangChain patch ───────────────────────────────────────────────────────────

def test_langchain_patch_sync_sends_event():
    from arsp_sdk._patches.langchain_patch import _wrap_run

    class FakeTool:
        name = "search"
        description = "web search"
        def _run(self, *a, **kw):
            return "result"

    client = MagicMock()
    _wrap_run(client, FakeTool)

    t = FakeTool()
    out = t._run(tool_input="query")
    assert out == "result"
    client.send.assert_called_once()
    meta = client.send.call_args.kwargs["metadata"]
    assert meta["framework"] == "langchain"
    assert meta["tool"] == "search"


def test_langchain_patch_records_error():
    from arsp_sdk._patches.langchain_patch import _wrap_run

    class BrokenTool:
        name = "broken"
        description = ""
        def _run(self, *a, **kw):
            raise ValueError("oops")

    client = MagicMock()
    _wrap_run(client, BrokenTool)

    with pytest.raises(ValueError):
        BrokenTool()._run()

    meta = client.send.call_args.kwargs["metadata"]
    assert meta["error"] == "oops"


# ── CrewAI patch ──────────────────────────────────────────────────────────────

def test_crewai_patch_execute_sync():
    from arsp_sdk._patches.crewai_patch import _wrap_task_execute_sync

    class FakeTask:
        name = "research"
        description = "Do research"
        expected_output = "A report"
        agent = None
        def execute_sync(self, *a, **kw):
            return "done"

    client = MagicMock()
    _wrap_task_execute_sync(client, FakeTask)

    t = FakeTask()
    result = t.execute_sync()
    assert result == "done"
    client.send.assert_called_once()
    meta = client.send.call_args.kwargs["metadata"]
    assert meta["framework"] == "crewai"
    assert meta["task"] == "research"
