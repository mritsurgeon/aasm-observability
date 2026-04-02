"""
Step 7 — Timeline API tests.
All PostgreSQL calls are mocked.
"""
import datetime
import pytest
from unittest.mock import AsyncMock, patch
import uuid

BASE_TS = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def _ts(offset_s: int = 0):
    return BASE_TS + datetime.timedelta(seconds=offset_s)


class FakeRow(dict):
    """Dict subclass that also supports attribute-style access used by asyncpg rows."""
    def __getitem__(self, k): return super().__getitem__(k)


def _fake_session_row(session_id: str, agent_id: str = "agent-1", offset_s: int = 0):
    return FakeRow({"session_id": session_id, "agent_id": agent_id, "start_time": _ts(offset_s)})


def _fake_event(session_id: str, type_: str = "tool_call", name: str = "test",
                offset_s: int = 0, parent_id=None):
    return FakeRow({
        "id":         uuid.uuid4(),
        "agent_id":   "agent-1",
        "session_id": session_id,
        "type":       type_,
        "name":       name,
        "timestamp":  _ts(offset_s),
        "metadata":   {},
        "parent_id":  parent_id,
    })


# ── GET /timeline ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timeline_empty():
    from app.timeline import get_timeline

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_timeline()

    assert result["sessions"] == []
    assert result["total_events"] == 0


@pytest.mark.asyncio
async def test_timeline_groups_by_session():
    from app.timeline import get_timeline

    session_rows = [
        _fake_session_row("s1", offset_s=0),
        _fake_session_row("s2", offset_s=120),
    ]
    event_rows = [
        _fake_event("s1", offset_s=0),
        _fake_event("s1", offset_s=5),
        _fake_event("s2", offset_s=120),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.side_effect = [session_rows, event_rows]

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_timeline(agent_id=None, session_id=None, limit=20)

    assert result["total_events"] == 3
    assert len(result["sessions"]) == 2


@pytest.mark.asyncio
async def test_timeline_duration_calculated():
    from app.timeline import get_timeline

    event_rows = [
        _fake_event("s1", offset_s=0),
        _fake_event("s1", offset_s=10),  # 10 seconds apart
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.side_effect = [[_fake_session_row("s1")], event_rows]

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_timeline(agent_id=None, session_id=None, limit=20)

    sess = result["sessions"][0]
    assert sess["duration_ms"] == 10_000


@pytest.mark.asyncio
async def test_timeline_session_id_filter():
    from app.timeline import get_timeline

    event_rows = [
        _fake_event("specific-session", offset_s=0),
        _fake_event("specific-session", offset_s=2),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = event_rows

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_timeline(session_id="specific-session")

    # With session_id filter, skips the session-list query
    assert mock_pg.fetch.call_count == 1
    assert result["sessions"][0]["session_id"] == "specific-session"


@pytest.mark.asyncio
async def test_timeline_parent_id_preserved():
    from app.timeline import get_timeline

    parent_id = uuid.uuid4()
    event_rows = [
        _fake_event("s1", offset_s=0),
        _fake_event("s1", offset_s=1, parent_id=parent_id),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.side_effect = [[_fake_session_row("s1")], event_rows]

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_timeline(agent_id=None, session_id=None, limit=20)

    events = result["sessions"][0]["events"]
    assert events[1]["parent_id"] == str(parent_id)
    assert events[0]["parent_id"] is None


# ── GET /timeline/{session_id} ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_trace_not_found():
    from app.timeline import get_session_trace
    from fastapi import HTTPException

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.timeline.get_pg", return_value=mock_pg):
        with pytest.raises(HTTPException) as exc_info:
            await get_session_trace("nonexistent-session")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_session_trace_returns_single_session():
    from app.timeline import get_session_trace

    event_rows = [
        _fake_event("s-abc", type_="llm_call", name="gpt-4o", offset_s=0),
        _fake_event("s-abc", type_="tool_call", name="search", offset_s=3),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = event_rows

    with patch("app.timeline.get_pg", return_value=mock_pg):
        result = await get_session_trace("s-abc")

    assert result["session_id"] == "s-abc"
    assert result["event_count"] == 2
    assert result["events"][0]["type"] == "llm_call"
    assert result["events"][1]["type"] == "tool_call"
