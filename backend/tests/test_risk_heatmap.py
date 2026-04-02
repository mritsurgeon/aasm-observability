"""
Steps 8 & 9 — Risk Engine + Heatmap tests.
All DB calls mocked.
"""
import datetime
import pytest
from unittest.mock import AsyncMock, patch


UTC = datetime.timezone.utc
BASE = datetime.datetime.now(UTC).replace(microsecond=0)


def _ev(type_: str, name: str = "t", offset_s: int = 0, error: bool = False):
    return {
        "type":      type_,
        "name":      name,
        "timestamp": BASE + datetime.timedelta(seconds=offset_s),
        "metadata":  {"error": "boom"} if error else {},
    }


# ── _score_session ────────────────────────────────────────────────────────────

def test_score_empty():
    from app.risk import _score_session
    r = _score_session([])
    assert r["risk_score"] == 0.0
    assert r["insight"] == "normal"


def test_score_external_contacts():
    from app.risk import _score_session
    evs = [_ev("api_call"), _ev("network"), _ev("tool_call")]
    r = _score_session(evs)
    assert r["risk_score"] > 0
    assert any("external" in x for x in r["reasoning"])


def test_score_errors_raise_risk():
    from app.risk import _score_session
    evs = [_ev("tool_call", error=True)] * 5 + [_ev("tool_call")] * 5
    r = _score_session(evs)
    assert r["risk_score"] > 0
    assert any("Error rate" in x for x in r["reasoning"])


def test_score_high_volume():
    from app.risk import _score_session
    evs = [_ev("tool_call")] * 35
    r = _score_session(evs)
    assert any("volume" in x.lower() for x in r["reasoning"])


def test_score_clamped_to_one():
    from app.risk import _score_session
    # Worst-case: many externals, errors, volume, llm, memory
    evs = (
        [_ev("api_call", error=True)] * 10 +
        [_ev("network",  error=True)] * 10 +
        [_ev("llm_call")] * 5 +
        [_ev("memory")]   * 5
    )
    r = _score_session(evs)
    assert r["risk_score"] <= 1.0


def test_score_critical_insight():
    from app.risk import _score_session
    evs = [_ev("api_call", error=True)] * 10 + [_ev("network")] * 5
    r = _score_session(evs)
    assert r["insight"] in ("critical_risk", "elevated_risk")


def test_score_llm_flagged():
    from app.risk import _score_session
    evs = [_ev("llm_call", name="gpt-4o")]
    r = _score_session(evs)
    assert any("LLM" in x for x in r["reasoning"])


# ── GET /risk/sessions ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_risk_sessions_empty():
    from app.risk import risk_sessions

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.risk.get_pg", return_value=mock_pg):
        result = await risk_sessions(limit=20)

    assert result["sessions"] == []
    assert result["summary"]["total"] == 0


@pytest.mark.asyncio
async def test_risk_sessions_returns_scores():
    from app.risk import risk_sessions

    class Row(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    session_row = Row({
        "session_id": "s1", "agent_id": "a1",
        "start_time": BASE, "event_count": 3,
    })
    event_rows = [
        Row({"type": "tool_call", "name": "search", "timestamp": BASE, "metadata": {}}),
        Row({"type": "api_call",  "name": "http",   "timestamp": BASE, "metadata": {}}),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.side_effect = [[session_row], event_rows]

    with patch("app.risk.get_pg", return_value=mock_pg):
        result = await risk_sessions(limit=20)

    assert len(result["sessions"]) == 1
    sess = result["sessions"][0]
    assert sess["session_id"] == "s1"
    assert sess["risk_score"] > 0   # api_call present


@pytest.mark.asyncio
async def test_risk_agents_empty():
    from app.risk import risk_agents

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.risk.get_pg", return_value=mock_pg):
        result = await risk_agents()

    assert result["agents"] == []


# ── GET /heatmap ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_heatmap_empty():
    from app.heatmap import get_heatmap

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.heatmap.get_pg", return_value=mock_pg):
        result = await get_heatmap(buckets=12, bucket_minutes=5)

    assert result["total_events"] == 0
    assert len(result["rows"]) == 6        # one row per event type
    assert len(result["bucket_labels"]) == 12


@pytest.mark.asyncio
async def test_heatmap_counts_events():
    from app.heatmap import get_heatmap

    class Row(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    now = datetime.datetime.now(UTC)
    recent = Row({"type": "tool_call", "timestamp": now, "has_error": None})

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = [recent, recent]  # 2 events

    with patch("app.heatmap.get_pg", return_value=mock_pg):
        result = await get_heatmap(buckets=12, bucket_minutes=5)

    assert result["total_events"] == 2
    tool_row = next(r for r in result["rows"] if r["type"] == "tool_call")
    # Last bucket should have count=2
    assert tool_row["cells"][-1]["count"] == 2


@pytest.mark.asyncio
async def test_heatmap_risk_score_nonzero_on_errors():
    from app.heatmap import get_heatmap

    class Row(dict):
        def __getitem__(self, k): return super().__getitem__(k)

    now = datetime.datetime.now(UTC)
    rows = [
        Row({"type": "api_call", "timestamp": now, "has_error": "boom"}),
        Row({"type": "api_call", "timestamp": now, "has_error": None}),
    ]

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = rows

    with patch("app.heatmap.get_pg", return_value=mock_pg):
        result = await get_heatmap(buckets=12, bucket_minutes=5)

    api_row = next(r for r in result["rows"] if r["type"] == "api_call")
    last_cell = api_row["cells"][-1]
    assert last_cell["error_count"] == 1
    assert last_cell["risk_score"] > 0


@pytest.mark.asyncio
async def test_heatmap_bucket_labels_count():
    from app.heatmap import get_heatmap

    mock_pg = AsyncMock()
    mock_pg.fetch.return_value = []

    with patch("app.heatmap.get_pg", return_value=mock_pg):
        result = await get_heatmap(buckets=6, bucket_minutes=10)

    assert len(result["bucket_labels"]) == 6
    assert result["buckets"] == 6
    assert result["bucket_minutes"] == 10
