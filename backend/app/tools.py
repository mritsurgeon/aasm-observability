"""
Dynamic Tool Registry — Step 4
Tools are discovered automatically from ingested tool_call events.
No static definitions; everything is observed.

GET /tools/registry           — all discovered tools, grouped by namespace
GET /tools/namespaces         — namespace summary with counts + top tools
GET /tools/{name}             — single tool detail + call history
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.database import get_pg

log = logging.getLogger(__name__)
router = APIRouter(prefix="/tools", tags=["tools"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/registry")
async def get_registry(namespace: Optional[str] = Query(None)):
    """
    Return all discovered tools grouped by namespace.
    Pass ?namespace=aws to filter to one namespace.
    """
    pg = await get_pg()

    if namespace:
        rows = await pg.fetch(
            "SELECT * FROM tools WHERE namespace = $1 ORDER BY call_count DESC",
            namespace,
        )
    else:
        rows = await pg.fetch(
            "SELECT * FROM tools ORDER BY namespace, call_count DESC"
        )

    # Group by namespace
    grouped: dict[str, list] = {}
    for r in rows:
        d = _tool_row(r)
        grouped.setdefault(d["namespace"], []).append(d)

    return {
        "total_tools":      len(rows),
        "total_namespaces": len(grouped),
        "namespaces":       grouped,
    }


@router.get("/namespaces")
async def get_namespaces():
    """Namespace-level rollup: call totals, error rates, tool counts, top tools."""
    pg = await get_pg()
    rows = await pg.fetch(
        """
        SELECT
            namespace,
            COUNT(*)                          AS tool_count,
            SUM(call_count)                   AS total_calls,
            SUM(error_count)                  AS total_errors,
            MAX(last_seen)                    AS last_seen,
            MIN(first_seen)                   AS first_seen,
            ARRAY_AGG(name ORDER BY call_count DESC) AS tools
        FROM tools
        GROUP BY namespace
        ORDER BY total_calls DESC
        """
    )
    return [
        {
            "namespace":    r["namespace"],
            "tool_count":   r["tool_count"],
            "total_calls":  r["total_calls"],
            "total_errors": r["total_errors"],
            "error_rate":   round(r["total_errors"] / r["total_calls"], 4)
                            if r["total_calls"] else 0.0,
            "first_seen":   r["first_seen"].isoformat() if r["first_seen"] else None,
            "last_seen":    r["last_seen"].isoformat()  if r["last_seen"]  else None,
            "top_tools":    list(r["tools"])[:5],
        }
        for r in rows
    ]


@router.get("/{name:path}")
async def get_tool(name: str):
    """Single tool: stats + 20 most recent calls."""
    pg = await get_pg()
    row = await pg.fetchrow("SELECT * FROM tools WHERE name = $1", name)
    if not row:
        raise HTTPException(404, detail=f"Tool '{name}' not found in registry")

    history = await pg.fetch(
        """
        SELECT id, agent_id, session_id, timestamp, metadata
        FROM   events
        WHERE  type = 'tool_call' AND name = $1
        ORDER  BY timestamp DESC
        LIMIT  20
        """,
        name,
    )

    return {
        **_tool_row(row),
        "recent_calls": [
            {
                "id":         str(h["id"]),
                "agent_id":   h["agent_id"],
                "session_id": h["session_id"],
                "timestamp":  h["timestamp"].isoformat(),
                "metadata":   dict(h["metadata"]),
            }
            for h in history
        ],
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tool_row(r) -> dict:
    return {
        "name":        r["name"],
        "namespace":   r["namespace"],
        "call_count":  r["call_count"],
        "error_count": r["error_count"],
        "error_rate":  round(r["error_count"] / r["call_count"], 4)
                       if r["call_count"] else 0.0,
        "agents":      list(r["agents"]),
        "first_seen":  r["first_seen"].isoformat() if r["first_seen"] else None,
        "last_seen":   r["last_seen"].isoformat()  if r["last_seen"]  else None,
    }
