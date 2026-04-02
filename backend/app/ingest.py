"""
Event Ingestion — Step 2
POST /events  — single event
POST /events/batch — multiple events (used by SDK)
GET  /events  — query with filters
GET  /events/{id} — single event
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.database import get_pg, get_neo4j
from app.events import event_bus

log = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["ingest"])

EVENT_TYPES = Literal["tool_call", "llm_call", "memory", "api_call", "network", "vector_db"]


# ── Tool helpers ──────────────────────────────────────────────────────────────

def _tool_namespace(name: str) -> str:
    """aws.s3.put_object → 'aws',  search_web → 'search_web'"""
    return name.split(".")[0] if "." in name else name


def _llm_provider(model: str) -> str:
    """Derive provider from model name (best-effort)."""
    m = model.lower()
    if m.startswith("gpt") or m.startswith("o1") or m.startswith("o3"):
        return "openai"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith("gemini"):
        return "google"
    if m.startswith("llama") or m.startswith("mistral") or m.startswith("mixtral"):
        return "open-source"
    if m.startswith("qwen") or m.startswith("phi") or m.startswith("deepseek") or m.startswith("nomic"):
        return "open-source"
    if "." in m:
        return m.split(".")[0]   # e.g. "llm.request" → "llm"
    return model


async def _upsert_tool(pg, name: str, agent_id: str, metadata: dict) -> None:
    error = metadata.get("error")
    await pg.execute(
        """
        INSERT INTO tools (name, namespace, call_count, error_count, agents, first_seen, last_seen)
        VALUES ($1, $2, 1, $3, ARRAY[$4], NOW(), NOW())
        ON CONFLICT (name) DO UPDATE SET
            call_count  = tools.call_count  + 1,
            error_count = tools.error_count + $3,
            agents      = (
                SELECT ARRAY(SELECT DISTINCT unnest(tools.agents || ARRAY[$4]))
            ),
            last_seen   = NOW()
        """,
        name,
        _tool_namespace(name),
        1 if error else 0,
        agent_id,
    )


# ── Schema ────────────────────────────────────────────────────────────────────

class EventRelationships(BaseModel):
    parent: Optional[str] = None
    related: list[str] = Field(default_factory=list)

class EventIn(BaseModel):
    id: Optional[str] = None
    agent_id:   str
    session_id: str
    type:       EVENT_TYPES
    name:       str
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata:   dict[str, Any] = {}
    parent_id:  Optional[str] = None
    relationships: Optional[EventRelationships] = None


class EventOut(EventIn):
    id:          str
    ingested_at: datetime


# ── Core write (Postgres + Neo4j + WebSocket) ─────────────────────────────────

async def _write_event(ev: EventIn) -> EventOut:
    pg = await get_pg()
    neo4j = await get_neo4j()

    # Resolve parent UUID
    effective_parent = ev.parent_id
    if ev.relationships and ev.relationships.parent:
        effective_parent = ev.relationships.parent

    parent_uuid = None
    if effective_parent:
        try:
            parent_uuid = uuid.UUID(effective_parent)
        except ValueError:
            pass
            
    # Resolve optional event ID
    event_id = ev.id if ev.id else str(uuid.uuid4())

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    row = await pg.fetchrow(
        """
        INSERT INTO events (id, agent_id, session_id, type, name, timestamp, metadata, parent_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, ingested_at
        """,
        uuid.UUID(event_id),
        ev.agent_id,
        ev.session_id,
        ev.type,
        ev.name,
        ev.timestamp,
        ev.metadata,      # pass dict directly — asyncpg JSONB codec handles encoding
        parent_uuid,
    )
    event_id   = str(row["id"])
    ingested_at = row["ingested_at"]

    # ── Neo4j (best-effort) ───────────────────────────────────────────────────
    if neo4j:
        try:
            async with neo4j.session() as s:
                await s.run(
                    """
                    MERGE (a:Agent {agent_id: $agent_id})
                    MERGE (sess:Session {session_id: $session_id})
                    MERGE (a)-[:RUNS]->(sess)
                    CREATE (e:Event {
                        event_id:   $event_id,
                        type:       $type,
                        name:       $name,
                        timestamp:  $timestamp,
                        metadata:   $metadata_json,
                        agent_id:   $agent_id,
                        session_id: $session_id
                    })
                    CREATE (sess)-[:NEXT]->(e)
                    WITH e
                    OPTIONAL MATCH (prev:Event {event_id: $parent_id})
                    FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
                        CREATE (e)-[:FOLLOWS]->(prev)
                    )
                    WITH e
                    UNWIND $related_ids AS rel_id
                    OPTIONAL MATCH (rel_event:Event {event_id: rel_id})
                    FOREACH (_ IN CASE WHEN rel_event IS NOT NULL THEN [1] ELSE [] END |
                        CREATE (e)-[:RELATED_TO]->(rel_event)
                    )
                    """,
                    agent_id=ev.agent_id,
                    session_id=ev.session_id,
                    event_id=event_id,
                    type=ev.type,
                    name=ev.name,
                    timestamp=ev.timestamp.isoformat(),
                    metadata_json=json.dumps(ev.metadata),
                    parent_id=effective_parent or "",
                    related_ids=(ev.relationships.related if ev.relationships else []),
                )
                
                # Tool node + CALLED relationship
                if ev.type == "tool_call":
                    await s.run(
                        """
                        MERGE (t:Tool {name: $name})
                        SET   t.namespace    = $namespace,
                              t.last_seen    = $timestamp,
                              t.description  = $description,
                              t.framework    = $framework,
                              t.last_input   = $last_input
                        MERGE (ns:Namespace {name: $namespace})
                        MERGE (t)-[:IN_NAMESPACE]->(ns)
                        WITH t
                        MATCH (sess:Session {session_id: $session_id})
                        MERGE (sess)-[:CALLS]->(t)
                        """,
                        name=ev.name,
                        namespace=_tool_namespace(ev.name),
                        timestamp=ev.timestamp.isoformat(),
                        session_id=ev.session_id,
                        description=str(ev.metadata.get("description", ""))[:200],
                        framework=str(ev.metadata.get("framework", ""))[:50],
                        last_input=str(ev.metadata.get("input", ""))[:200],
                    )

                # LLMModel node + CALLS relationship
                elif ev.type == "llm_call":
                    model    = ev.metadata.get("model", ev.name)
                    provider = _llm_provider(model)
                    usage    = ev.metadata.get("usage", {}) or {}
                    await s.run(
                        """
                        MERGE (m:LLMModel {name: $model})
                        SET   m.last_seen          = $timestamp,
                              m.provider           = $provider,
                              m.framework          = $framework,
                              m.last_prompt_tokens = $prompt_tokens,
                              m.last_completion_tokens = $completion_tokens
                        WITH m
                        MATCH (sess:Session {session_id: $session_id})
                        MERGE (sess)-[:CALLS]->(m)
                        """,
                        model=model,
                        timestamp=ev.timestamp.isoformat(),
                        session_id=ev.session_id,
                        provider=provider,
                        framework=str(ev.metadata.get("framework", ""))[:50],
                        prompt_tokens=int(usage.get("prompt_tokens", 0)),
                        completion_tokens=int(usage.get("completion_tokens", 0)),
                    )

                # Memory node
                elif ev.type == "memory":
                    await s.run(
                        """
                        MERGE (m:Memory {event_id: $event_id})
                        SET   m.name = $name, m.timestamp = $timestamp
                        WITH m
                        MATCH (sess:Session {session_id: $session_id})
                        MERGE (sess)-[:WRITES]->(m)
                        """,
                        event_id=event_id,
                        name=ev.name,
                        timestamp=ev.timestamp.isoformat(),
                        session_id=ev.session_id,
                    )

                # VectorDB node
                elif ev.type == "vector_db":
                    db_type   = ev.metadata.get("db_type") or ev.metadata.get("provider", "")
                    collection = ev.metadata.get("collection") or ev.metadata.get("index", "")
                    await s.run(
                        """
                        MERGE (v:VectorDB {name: $name})
                        SET   v.last_seen   = $timestamp,
                              v.db_type     = $db_type,
                              v.collection  = $collection,
                              v.last_op     = $operation
                        WITH v
                        MATCH (sess:Session {session_id: $session_id})
                        MERGE (sess)-[:QUERIES]->(v)
                        """,
                        name=ev.name,
                        timestamp=ev.timestamp.isoformat(),
                        session_id=ev.session_id,
                        db_type=str(db_type)[:100],
                        collection=str(collection)[:200],
                        operation=str(ev.metadata.get("operation", ev.name))[:100],
                    )

                # ExternalSystem node + CONNECTS_TO relationship
                elif ev.type in ("api_call", "network"):
                    host   = ev.metadata.get("host") or ev.metadata.get("url", ev.name)
                    method = ev.metadata.get("method", "")
                    status = ev.metadata.get("status_code") or ev.metadata.get("status", "")
                    await s.run(
                        """
                        MERGE (ext:ExternalSystem {host: $host})
                        SET   ext.last_seen   = $timestamp,
                              ext.last_method = $method,
                              ext.last_status = $status
                        WITH ext
                        MATCH (sess:Session {session_id: $session_id})
                        MERGE (sess)-[:CONNECTS_TO]->(ext)
                        """,
                        host=str(host)[:200],
                        timestamp=ev.timestamp.isoformat(),
                        session_id=ev.session_id,
                        method=str(method)[:20],
                        status=str(status)[:20],
                    )
        except Exception as exc:
            log.warning("Neo4j write failed (event %s): %s", event_id, exc)

    # ── Tool registry (best-effort) ───────────────────────────────────────────
    if ev.type == "tool_call":
        try:
            await _upsert_tool(pg, ev.name, ev.agent_id, ev.metadata)
        except Exception as exc:
            log.warning("Tool upsert failed: %s", exc)

    # ── WebSocket broadcast ───────────────────────────────────────────────────
    ws_payload = {
        "id":         event_id,
        "agent_id":   ev.agent_id,
        "session_id": ev.session_id,
        "type":       ev.type,
        "name":       ev.name,
        "timestamp":  ev.timestamp.isoformat(),
        "metadata":   ev.metadata,
        "parent_id":  ev.parent_id,
    }
    await event_bus.emit(ev.type, ws_payload)

    return EventOut(
        id=event_id, ingested_at=ingested_at,
        **ev.model_dump(),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201, response_model=EventOut)
async def ingest_event(ev: EventIn):
    return await _write_event(ev)


@router.post("/batch", status_code=201)
async def ingest_batch(events: list[EventIn]):
    results = []
    for ev in events:
        results.append(await _write_event(ev))
    return {"count": len(results), "events": [e.model_dump() for e in results]}


@router.get("", response_model=list[dict])
async def query_events(
    agent_id:   Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    type:       Optional[str] = Query(None),
    limit:      int           = Query(100, le=1000),
):
    pg = await get_pg()
    filters, params = [], []
    if agent_id:
        params.append(agent_id);   filters.append(f"agent_id = ${len(params)}")
    if session_id:
        params.append(session_id); filters.append(f"session_id = ${len(params)}")
    if type:
        params.append(type);       filters.append(f"type = ${len(params)}")
    params.append(limit)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    rows = await pg.fetch(
        f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ${len(params)}",
        *params,
    )
    return [dict(r) for r in rows]


@router.get("/recent")
async def get_recent_events():
    """In-memory recent events for WebSocket replay."""
    return {"count": len(event_bus.recent()), "events": event_bus.recent()}


@router.get("/{event_id}")
async def get_event(event_id: str):
    pg = await get_pg()
    row = await pg.fetchrow("SELECT * FROM events WHERE id = $1", uuid.UUID(event_id))
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, detail="Event not found")
    return dict(row)
