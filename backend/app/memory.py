"""
Memory System — Step 4
Versioned, hash-chained memory with rollback support.

Each entry stores:
  - content
  - timestamp (UTC ISO-8601)
  - sha256 hash  (of content + parent_id + timestamp)
  - parent_id    (id of the previous HEAD, or None for genesis)

Rollback sets the HEAD pointer to a past entry; all entries after
the rollback point are retained in history but marked inactive.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.events import event_bus

router = APIRouter(prefix="/memory", tags=["memory"])

# ── Storage ───────────────────────────────────────────────────────────────────
# Ordered list of all entries ever written (append-only log).
_chain: list[dict[str, Any]] = []
# id of the current HEAD entry (latest active entry).
_head_id: str | None = None
# Set of ids that are currently "active" (not rolled past).
_active_ids: set[str] = set()


def _compute_hash(content: str, parent_id: str | None, timestamp: str) -> str:
    raw = f"{content}|{parent_id or 'genesis'}|{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_entry(entry_id: str) -> dict[str, Any] | None:
    for e in _chain:
        if e["id"] == entry_id:
            return e
    return None


# ── Public helpers (used by agent.py in later steps) ─────────────────────────
def memory_write(content: str, agent_id: str = "system") -> dict[str, Any]:
    """Write a new entry and return it. Updates HEAD."""
    global _head_id
    entry_id = str(uuid.uuid4())
    ts = datetime.now(timezone.utc).isoformat()
    parent_id = _head_id
    sha = _compute_hash(content, parent_id, ts)

    entry: dict[str, Any] = {
        "id": entry_id,
        "content": content,
        "timestamp": ts,
        "hash": sha,
        "parent_id": parent_id,
        "agent_id": agent_id,
        "active": True,
    }
    _chain.append(entry)
    _active_ids.add(entry_id)
    _head_id = entry_id
    return entry


def memory_read_all() -> list[dict[str, Any]]:
    return list(_chain)


def memory_head() -> dict[str, Any] | None:
    return _get_entry(_head_id) if _head_id else None


# ── Request models ────────────────────────────────────────────────────────────
class MemoryWriteRequest(BaseModel):
    content: str
    agent_id: str = "system"


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.post("/write")
async def write_memory(req: MemoryWriteRequest):
    entry = memory_write(req.content, req.agent_id)
    await event_bus.emit("memory_write", entry)
    return {"status": "written", "entry": entry, "head_id": _head_id}


@router.get("")
async def get_memory_chain():
    """Return the full chain, active flag, and HEAD pointer."""
    return {
        "head_id": _head_id,
        "count": len(_chain),
        "chain": _chain,
    }


@router.get("/head")
async def get_head():
    head = memory_head()
    if not head:
        raise HTTPException(status_code=404, detail="Memory is empty")
    return head


@router.post("/rollback/{entry_id}")
async def rollback(entry_id: str):
    """
    Roll back HEAD to `entry_id`.
    All entries after that point are marked inactive.
    The rollback itself is recorded as a new active entry.
    """
    global _head_id, _active_ids

    target = _get_entry(entry_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")

    # Find the position of the target in the chain
    target_idx = next(i for i, e in enumerate(_chain) if e["id"] == entry_id)

    # Mark everything after target as inactive
    deactivated = []
    for entry in _chain[target_idx + 1 :]:
        if entry["active"]:
            entry["active"] = False
            _active_ids.discard(entry["id"])
            deactivated.append(entry["id"])

    # Write a rollback marker entry
    rollback_entry = memory_write(
        content=f"[ROLLBACK] Restored state to entry {entry_id}: \"{target['content'][:100]}\"",
        agent_id="system",
    )
    # The rollback entry's parent is the target (reflect true lineage)
    rollback_entry["parent_id"] = entry_id
    rollback_entry["hash"] = _compute_hash(
        rollback_entry["content"], entry_id, rollback_entry["timestamp"]
    )

    return {
        "status": "rolled_back",
        "restored_entry": target,
        "deactivated_ids": deactivated,
        "rollback_marker": rollback_entry,
        "new_head_id": _head_id,
    }
