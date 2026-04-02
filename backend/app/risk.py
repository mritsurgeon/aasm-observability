"""
Risk Engine — Step 8 (passive intelligence, no blocking)

Reads ingested events from PostgreSQL and computes risk signals.
Never blocks or modifies agent behaviour — observation only.

Risk factors (all additive, clamped to 1.0):
  • External contacts (api_call / network)   +0.12 each, max 0.36
  • Error rate                                error_rate * 0.30
  • High call volume (>30 events/session)     +0.15
  • Wide namespace spread (>5 namespaces)     +0.10
  • LLM calls present                         +0.08
  • Memory writes present                     +0.05
  • Off-hours activity (UTC 22–06)            +0.05

GET /risk/sessions              — risk for recent sessions
GET /risk/session/{session_id}  — detailed breakdown for one session
GET /risk/agents                — per-agent aggregated risk
"""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.database import get_pg


def _parse_meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}

log = logging.getLogger(__name__)
router = APIRouter(prefix="/risk", tags=["risk"])

THRESHOLD_ALERT = 0.45
THRESHOLD_CRITICAL = 0.75


# ── Core scorer (pure, no I/O) ────────────────────────────────────────────────

def _score_session(events: list[dict]) -> dict:
    """Compute risk score from a list of event dicts (already fetched)."""
    reasoning: list[str] = []
    score = 0.0

    total     = len(events)
    errors    = sum(1 for e in events if e.get("metadata", {}).get("error"))
    ext_calls = sum(1 for e in events if e["type"] in ("api_call", "network"))
    llm_calls = sum(1 for e in events if e["type"] == "llm_call")
    mem_writes= sum(1 for e in events if e["type"] == "memory")

    namespaces = {
        e["name"].split(".")[0]
        for e in events
        if e["type"] == "tool_call"
    }

    # External contacts
    if ext_calls:
        boost = min(ext_calls * 0.12, 0.36)
        score += boost
        reasoning.append(f"{ext_calls} external contact(s) (+{boost:.2f})")

    # Error rate
    if total and errors:
        rate  = errors / total
        boost = round(rate * 0.30, 3)
        score += boost
        reasoning.append(f"Error rate {rate:.0%} (+{boost:.2f})")

    # High volume
    if total > 30:
        score += 0.15
        reasoning.append(f"High event volume: {total} events (+0.15)")

    # Namespace spread
    if len(namespaces) > 5:
        score += 0.10
        reasoning.append(f"{len(namespaces)} tool namespaces — wide blast radius (+0.10)")

    # LLM present
    if llm_calls:
        score += 0.08
        reasoning.append(f"{llm_calls} LLM call(s) — data exposure risk (+0.08)")

    # Memory writes
    if mem_writes:
        score += 0.05
        reasoning.append(f"{mem_writes} memory write(s) (+0.05)")

    # Off-hours (UTC)
    if events:
        ts = events[0].get("timestamp")
        if ts:
            hour = ts.hour if isinstance(ts, datetime) else datetime.fromisoformat(str(ts)).hour
            if hour >= 22 or hour < 6:
                score += 0.05
                reasoning.append("Off-hours activity UTC (+0.05)")

    score = round(min(score, 1.0), 4)
    insight = (
        "critical_risk"  if score >= THRESHOLD_CRITICAL else
        "elevated_risk"  if score >= THRESHOLD_ALERT    else
        "normal"
    )

    return {
        "risk_score":   score,
        "insight":      insight,
        "confidence":   round(min(0.50 + 0.08 * len(reasoning), 0.99), 4),
        "reasoning":    reasoning,
        "stats": {
            "total_events": total,
            "errors":        errors,
            "ext_calls":     ext_calls,
            "llm_calls":     llm_calls,
            "mem_writes":    mem_writes,
            "namespaces":    sorted(namespaces),
        },
        "thresholds": {"alert": THRESHOLD_ALERT, "critical": THRESHOLD_CRITICAL},
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _events_for_session(pg, session_id: str) -> list[dict]:
    rows = await pg.fetch(
        "SELECT type, name, timestamp, metadata FROM events WHERE session_id = $1 ORDER BY timestamp",
        session_id,
    )
    return [{"type": r["type"], "name": r["name"],
             "timestamp": r["timestamp"], "metadata": _parse_meta(r["metadata"])} for r in rows]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/sessions")
async def risk_sessions(limit: int = Query(20, le=100)):
    """Risk summary for the most recent sessions."""
    pg = await get_pg()

    session_rows = await pg.fetch(
        """
        SELECT session_id, agent_id, MIN(timestamp) AS start_time, COUNT(*) AS event_count
        FROM   events
        GROUP  BY session_id, agent_id
        ORDER  BY start_time DESC
        LIMIT  $1
        """,
        limit,
    )

    results = []
    for sr in session_rows:
        evs   = await _events_for_session(pg, sr["session_id"])
        risk  = _score_session(evs)
        results.append({
            "session_id":  sr["session_id"],
            "agent_id":    sr["agent_id"],
            "start_time":  sr["start_time"].isoformat(),
            "event_count": sr["event_count"],
            **risk,
        })

    return {
        "sessions": results,
        "summary": {
            "total":    len(results),
            "critical": sum(1 for r in results if r["insight"] == "critical_risk"),
            "elevated": sum(1 for r in results if r["insight"] == "elevated_risk"),
            "normal":   sum(1 for r in results if r["insight"] == "normal"),
        },
    }


@router.get("/session/{session_id}")
async def risk_session(session_id: str):
    """Detailed risk breakdown for a single session."""
    pg  = await get_pg()
    evs = await _events_for_session(pg, session_id)
    if not evs:
        raise HTTPException(404, detail=f"Session '{session_id}' not found")

    risk = _score_session(evs)

    # Augment with per-event risk flags
    flagged = [
        {
            "type":      e["type"],
            "name":      e["name"],
            "timestamp": e["timestamp"].isoformat() if isinstance(e["timestamp"], datetime)
                         else str(e["timestamp"]),
            "flag":      "error"    if e["metadata"].get("error")        else
                         "external" if e["type"] in ("api_call","network") else
                         "llm"      if e["type"] == "llm_call"            else None,
        }
        for e in evs
        if e["metadata"].get("error") or e["type"] in ("api_call","network","llm_call")
    ]

    return {"session_id": session_id, "flagged_events": flagged, **risk}


@router.get("/agents")
async def risk_agents():
    """Per-agent aggregated risk across all their sessions."""
    pg = await get_pg()

    agent_rows = await pg.fetch(
        """
        SELECT agent_id, COUNT(DISTINCT session_id) AS session_count,
               COUNT(*) AS total_events
        FROM   events
        GROUP  BY agent_id
        ORDER  BY total_events DESC
        """
    )

    results = []
    for ar in agent_rows:
        evs  = await pg.fetch(
            "SELECT type, name, timestamp, metadata FROM events WHERE agent_id = $1 ORDER BY timestamp",
            ar["agent_id"],
        )
        ev_dicts = [{"type": r["type"], "name": r["name"],
                     "timestamp": r["timestamp"], "metadata": _parse_meta(r["metadata"])} for r in evs]
        risk = _score_session(ev_dicts)
        results.append({
            "agent_id":       ar["agent_id"],
            "session_count":  ar["session_count"],
            "total_events":   ar["total_events"],
            **risk,
        })

    return {"agents": results}
