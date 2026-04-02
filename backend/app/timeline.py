"""
Timeline API — Step 7
Returns real execution traces grouped by session, ordered chronologically,
with parent→child chain information for rendering sequence diagrams.

GET /timeline                — all recent sessions with their event chains
GET /timeline/{session_id}  — single session full trace
"""
import json
import logging
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Query

from app.database import get_pg

log = logging.getLogger(__name__)
router = APIRouter(prefix="/timeline", tags=["timeline"])


def _parse_meta(raw) -> dict:
    """Safely coerce an asyncpg JSONB value to a Python dict."""
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


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def get_timeline(
    agent_id:   Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    limit:      int           = Query(20, le=100, description="Max sessions to return"),
):
    """
    Returns sessions with their ordered event chains.
    Each session includes: events in order, duration, start/end timestamps.
    """
    pg = await get_pg()

    # Resolve which sessions to include
    if session_id:
        session_ids = [session_id]
    else:
        filters, params = [], []
        if agent_id:
            params.append(agent_id)
            filters.append(f"agent_id = ${len(params)}")
        params.append(limit)
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        rows = await pg.fetch(
            f"""
            SELECT DISTINCT session_id, agent_id,
                   MIN(timestamp) AS start_time
            FROM events
            {where}
            GROUP BY session_id, agent_id
            ORDER BY start_time DESC
            LIMIT ${len(params)}
            """,
            *params,
        )
        session_ids = [r["session_id"] for r in rows]

    if not session_ids:
        return {"sessions": [], "total_events": 0}

    # Fetch all events for these sessions in one query
    placeholders = ", ".join(f"${i+1}" for i in range(len(session_ids)))
    event_rows = await pg.fetch(
        f"""
        SELECT id, agent_id, session_id, type, name,
               timestamp, metadata, parent_id
        FROM   events
        WHERE  session_id IN ({placeholders})
        ORDER  BY session_id, timestamp ASC
        """,
        *session_ids,
    )

    # Group events by session
    by_session: dict[str, list] = {}
    for r in event_rows:
        sid = r["session_id"]
        by_session.setdefault(sid, []).append(r)

    sessions = []
    for sid in session_ids:
        evs = by_session.get(sid, [])
        if not evs:
            continue

        start = evs[0]["timestamp"]
        end   = evs[-1]["timestamp"]

        # Ensure timezone-aware for subtraction
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        duration_ms = int((end - start).total_seconds() * 1000)

        sessions.append({
            "session_id":   sid,
            "agent_id":     evs[0]["agent_id"],
            "event_count":  len(evs),
            "start":        start.isoformat(),
            "end":          end.isoformat(),
            "duration_ms":  duration_ms,
            "events": [
                {
                    "id":        str(e["id"]),
                    "type":      e["type"],
                    "name":      e["name"],
                    "timestamp": e["timestamp"].isoformat()
                                 if e["timestamp"].tzinfo
                                 else e["timestamp"].replace(tzinfo=timezone.utc).isoformat(),
                    "parent_id": str(e["parent_id"]) if e["parent_id"] else None,
                    "metadata":  _parse_meta(e["metadata"]),
                }
                for e in evs
            ],
        })

    return {
        "sessions":     sessions,
        "total_events": sum(s["event_count"] for s in sessions),
    }


@router.get("/{session_id}")
async def get_session_trace(session_id: str):
    """Full execution trace for a single session."""
    result = await get_timeline(session_id=session_id, limit=1)
    sessions = result["sessions"]
    if not sessions:
        from fastapi import HTTPException
        raise HTTPException(404, detail=f"Session '{session_id}' not found")
    return sessions[0]
