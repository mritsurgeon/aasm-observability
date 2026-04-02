"""
Step 5 — Neo4j Graph Schema tests.
All Neo4j calls are mocked; tests verify Cypher is invoked correctly
and the response shape matches the UI's expectations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helper: fake Neo4j node ───────────────────────────────────────────────────

def _fake_node(label: str, props: dict, eid: str = None):
    node = MagicMock()
    node.element_id = eid or f"4:{label.lower()}:1"
    node.labels = {label}
    node.__iter__ = lambda self: iter(props.items())
    node.__getitem__ = lambda self, k: props[k]
    node.keys = lambda: list(props.keys())
    node.get = lambda k, d=None: props.get(k, d)
    # Make dict(node) work via __iter__
    node._props = props
    return node


def _fake_record(**kwargs):
    rec = MagicMock()
    rec.__getitem__ = lambda self, k: kwargs.get(k)
    return rec


class FakeResult:
    """Async iterable of fake records."""
    def __init__(self, records):
        self._records = records

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for r in self._records:
            yield r

    async def single(self):
        return self._records[0] if self._records else None


# ── /graph/schema ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schema_neo4j_unavailable():
    from app.graph import graph_schema
    with patch("app.graph.get_neo4j", return_value=None):
        result = await graph_schema()
    assert result["node_labels"] == {}
    assert "error" in result


@pytest.mark.asyncio
async def test_schema_returns_counts():
    from app.graph import graph_schema

    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    label_rec = MagicMock(); label_rec.__getitem__ = lambda self, k: "Agent"
    count_rec = MagicMock(); count_rec.__getitem__ = lambda self, k: 3

    mock_session.run.side_effect = [
        FakeResult([label_rec]),           # db.labels()
        FakeResult([count_rec]),           # count Agent
        FakeResult([]),                    # db.relationshipTypes()
    ]

    with patch("app.graph.get_neo4j", return_value=mock_driver):
        result = await graph_schema()

    assert "Agent" in result["node_labels"]
    assert result["node_labels"]["Agent"] == 3


# ── /graph/overview ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_overview_neo4j_unavailable():
    from app.graph import graph_overview
    with patch("app.graph.get_neo4j", return_value=None):
        result = await graph_overview()
    assert result["nodes"] == []
    assert result["edges"] == []


@pytest.mark.asyncio
async def test_overview_empty_graph():
    from app.graph import graph_overview

    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session.run.return_value = FakeResult([])

    with patch("app.graph.get_neo4j", return_value=mock_driver):
        result = await graph_overview()

    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["counts"]["nodes"] == 0


# ── /graph/agent/{agent_id} ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_not_found():
    from app.graph import graph_agent
    from fastapi import HTTPException

    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session.run.return_value = FakeResult([])

    with patch("app.graph.get_neo4j", return_value=mock_driver):
        with pytest.raises(HTTPException) as exc_info:
            await graph_agent("missing-agent")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_agent_subgraph_deduplicates_edges():
    from app.graph import graph_agent

    agent_node = _fake_node("Agent", {"agent_id": "a1"}, "4:agent:1")
    tool_node  = _fake_node("Tool",  {"name": "search"}, "4:tool:1")

    # Two records with same agent→tool relationship
    rec1 = _fake_record(a=agent_node, sess=None, t=tool_node, ns=None, m=None, ext=None)
    rec2 = _fake_record(a=agent_node, sess=None, t=tool_node, ns=None, m=None, ext=None)

    mock_session = AsyncMock()
    mock_driver = MagicMock()
    mock_driver.session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_session.run.return_value = FakeResult([rec1, rec2])

    with patch("app.graph.get_neo4j", return_value=mock_driver):
        result = await graph_agent("a1")

    # Edges should be deduplicated
    edge_keys = [(e["source"], e["target"], e["type"]) for e in result["edges"]]
    assert len(edge_keys) == len(set(edge_keys))


# ── _tool_namespace (ingest helper) ───────────────────────────────────────────

def test_namespace_extraction():
    from app.ingest import _tool_namespace
    assert _tool_namespace("aws.s3.put") == "aws"
    assert _tool_namespace("search")     == "search"
    assert _tool_namespace("db.query")   == "db"


# ── Neo4j schema DDL in database.py ───────────────────────────────────────────

def test_new_constraints_present():
    """Verify all 6 node constraints are in the init_neo4j source."""
    import inspect
    from app import database
    src = inspect.getsource(database.init_neo4j)
    for label in ["Agent", "Session", "Tool", "LLMModel", "ExternalSystem", "Namespace"]:
        assert label in src, f"Missing constraint for {label}"


def test_event_indexes_present():
    import inspect
    from app import database
    src = inspect.getsource(database.init_neo4j)
    for idx in ["event_type", "event_ts", "event_agent"]:
        assert idx in src, f"Missing index {idx}"
