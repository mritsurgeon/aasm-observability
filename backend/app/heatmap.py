"""
Heatmap API — Step 9
Returns a time-bucketed grid of event counts and risk scores.

Axes:
  Y — event type  (tool_call, llm_call, memory, api_call, network, vector_db)
  X — time bucket (most-recent N buckets, each spanning bucket_minutes)

GET /heatmap?buckets=12&bucket_minutes=5
"""
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from app.database import get_pg

log = logging.getLogger(__name__)
router = APIRouter(prefix="/heatmap", tags=["heatmap"])

EVENT_TYPES = ["tool_call", "llm_call", "memory", "api_call", "network", "vector_db"]


@router.get("")
async def get_heatmap(
    buckets:        int = Query(12, ge=1,  le=48),
    bucket_minutes: int = Query(5,  ge=1,  le=60),
):
    """
    Returns rows × cols grid.
    Each cell: { count, error_count, risk_score }
    risk_score = error_count/count weighted by type danger factor.
    """
    pg  = await get_pg()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=buckets * bucket_minutes)

    rows = await pg.fetch(
        """
        SELECT
            type,
            timestamp,
            metadata->>'error' AS has_error
        FROM   events
        WHERE  timestamp >= $1
        ORDER  BY timestamp
        """,
        window_start,
    )

    # Bucket index: 0 = oldest, buckets-1 = most recent
    def bucket_of(ts: datetime) -> int:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        elapsed = (now - ts).total_seconds() / 60          # minutes ago
        idx = buckets - 1 - int(elapsed / bucket_minutes)  # flip: oldest left
        return max(0, min(buckets - 1, idx))

    # Build grid: type × bucket → {count, errors}
    grid: dict[str, list[dict]] = {
        t: [{"count": 0, "errors": 0} for _ in range(buckets)]
        for t in EVENT_TYPES
    }

    for r in rows:
        t = r["type"]
        if t not in grid:
            continue
        b   = bucket_of(r["timestamp"])
        cell = grid[t][b]
        cell["count"]  += 1
        if r["has_error"]:
            cell["errors"] += 1

    # Danger weights per type
    DANGER: dict[str, float] = {
        "tool_call": 0.3,
        "llm_call":  0.2,
        "memory":    0.15,
        "api_call":  0.5,
        "network":   0.5,
        "vector_db": 0.2,
    }

    # Compute risk score per cell
    output_rows = []
    for t in EVENT_TYPES:
        cells = []
        for cell in grid[t]:
            c = cell["count"]
            e = cell["errors"]
            if c == 0:
                risk = 0.0
            else:
                error_factor = (e / c) * 0.6
                volume_factor = min(c / 20, 1.0) * 0.4
                risk = round((error_factor + volume_factor) * DANGER[t], 4)
            cells.append({"count": c, "error_count": e, "risk_score": risk})
        output_rows.append({"type": t, "cells": cells})

    # Build bucket labels (oldest → newest)
    bucket_labels = []
    for i in range(buckets):
        ts = window_start + timedelta(minutes=i * bucket_minutes)
        bucket_labels.append(ts.strftime("%H:%M"))

    total_events = sum(r["count"] for row in output_rows for r in row["cells"])
    max_risk     = max((c["risk_score"] for row in output_rows for c in row["cells"]), default=0.0)

    return {
        "rows":          output_rows,
        "bucket_labels": bucket_labels,
        "bucket_minutes": bucket_minutes,
        "buckets":       buckets,
        "window_start":  window_start.isoformat(),
        "total_events":  total_events,
        "max_risk":      max_risk,
    }
