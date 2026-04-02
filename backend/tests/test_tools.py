"""
Step 4 — Dynamic Tool Registry tests.
Uses an in-memory SQLite-compatible approach via MagicMock for the pg pool,
and tests the helper logic directly.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── _tool_namespace ────────────────────────────────────────────────────────────

def test_namespace_dotted():
    from app.ingest import _tool_namespace
    assert _tool_namespace("aws.s3.put_object") == "aws"


def test_namespace_single_segment():
    from app.ingest import _tool_namespace
    assert _tool_namespace("search_web") == "search_web"


def test_namespace_two_parts():
    from app.ingest import _tool_namespace
    assert _tool_namespace("db.query") == "db"


# ── _upsert_tool (unit) ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_tool_calls_execute():
    from app.ingest import _upsert_tool

    pg = AsyncMock()
    await _upsert_tool(pg, "aws.s3.get", "agent-1", {})
    pg.execute.assert_awaited_once()
    sql, name, namespace, err_inc, agent_id = pg.execute.call_args.args
    assert name == "aws.s3.get"
    assert namespace == "aws"
    assert err_inc == 0
    assert agent_id == "agent-1"


@pytest.mark.asyncio
async def test_upsert_tool_increments_error():
    from app.ingest import _upsert_tool

    pg = AsyncMock()
    await _upsert_tool(pg, "db.query", "agent-2", {"error": "timeout"})
    _, _, _, err_inc, _ = pg.execute.call_args.args
    assert err_inc == 1


# ── /tools/registry endpoint ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_registry_groups_by_namespace():
    from app.tools import get_registry

    row1 = {"name": "aws.s3.get", "namespace": "aws", "call_count": 10,
            "error_count": 0, "agents": ["a1"], "first_seen": None, "last_seen": None}
    row2 = {"name": "aws.ec2.describe", "namespace": "aws", "call_count": 5,
            "error_count": 1, "agents": ["a1", "a2"], "first_seen": None, "last_seen": None}
    row3 = {"name": "search_web", "namespace": "search_web", "call_count": 3,
            "error_count": 0, "agents": ["a1"], "first_seen": None, "last_seen": None}

    class FakeRecord(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = [FakeRecord(r) for r in [row1, row2, row3]]

    with patch("app.tools.get_pg", return_value=mock_pg):
        result = await get_registry()

    assert result["total_tools"] == 3
    assert result["total_namespaces"] == 2
    assert "aws" in result["namespaces"]
    assert len(result["namespaces"]["aws"]) == 2


@pytest.mark.asyncio
async def test_registry_namespace_filter():
    from app.tools import get_registry

    class FakeRecord(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = [
        FakeRecord({"name": "db.query", "namespace": "db", "call_count": 7,
                    "error_count": 2, "agents": ["a1"], "first_seen": None, "last_seen": None})
    ]

    with patch("app.tools.get_pg", return_value=mock_pg):
        result = await get_registry(namespace="db")

    # Should pass namespace to query
    call_args = mock_pg.fetch.call_args
    assert "db" in call_args.args


@pytest.mark.asyncio
async def test_registry_empty():
    from app.tools import get_registry

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.tools.get_pg", return_value=mock_pg):
        result = await get_registry()

    assert result["total_tools"] == 0
    assert result["total_namespaces"] == 0
    assert result["namespaces"] == {}


# ── /tools/namespaces endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_namespaces_error_rate():
    from app.tools import get_namespaces
    import datetime

    class FakeRecord(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = [
        FakeRecord({
            "namespace": "aws", "tool_count": 3, "total_calls": 100,
            "total_errors": 10, "last_seen": datetime.datetime(2025, 1, 1),
            "first_seen": datetime.datetime(2024, 1, 1), "tools": ["aws.s3.get"]
        })
    ]

    with patch("app.tools.get_pg", return_value=mock_pg):
        result = await get_namespaces()

    assert len(result) == 1
    assert result[0]["error_rate"] == 0.1
    assert result[0]["namespace"] == "aws"
    assert result[0]["top_tools"] == ["aws.s3.get"]


# ── /tools/{name} endpoint ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_tool_not_found():
    from app.tools import get_tool
    from fastapi import HTTPException

    mock_pg = AsyncMock()
    mock_pg.fetchrow.return_value = None

    with patch("app.tools.get_pg", return_value=mock_pg):
        with pytest.raises(HTTPException) as exc_info:
            await get_tool("nonexistent.tool")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_tool_returns_history():
    from app.tools import get_tool
    import datetime

    class FakeRecord(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    tool_row = FakeRecord({
        "name": "search_web", "namespace": "search_web",
        "call_count": 5, "error_count": 0, "agents": ["a1"],
        "first_seen": datetime.datetime(2025, 1, 1),
        "last_seen": datetime.datetime(2025, 6, 1),
    })
    history_row = FakeRecord({
        "id": "00000000-0000-0000-0000-000000000001",
        "agent_id": "a1", "session_id": "s1",
        "timestamp": datetime.datetime(2025, 6, 1),
        "metadata": {"input": "test"},
    })

    mock_pg = AsyncMock()
    mock_pg.fetchrow.return_value = tool_row
    mock_pg.fetch.return_value = [history_row]

    with patch("app.tools.get_pg", return_value=mock_pg):
        result = await get_tool("search_web")

    assert result["name"] == "search_web"
    assert len(result["recent_calls"]) == 1
    assert result["recent_calls"][0]["agent_id"] == "a1"
