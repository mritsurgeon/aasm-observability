"""
Database connections — PostgreSQL (events store) + Neo4j (graph).

Both are initialised once on startup via FastAPI lifespan.
Neo4j writes are best-effort: if the graph DB is unavailable,
events are still accepted and stored in PostgreSQL.
"""

import asyncio
import json
import logging
import os
from typing import Optional

import asyncpg
from neo4j import AsyncGraphDatabase, AsyncDriver

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://arsp:arsp@localhost:5432/arsp")
NEO4J_URI    = os.getenv("NEO4J_URI",    "bolt://localhost:7687")
NEO4J_USER   = os.getenv("NEO4J_USER",   "neo4j")
NEO4J_PASS   = os.getenv("NEO4J_PASSWORD","arsp_password")

_pg_pool: Optional[asyncpg.Pool] = None
_neo4j:   Optional[AsyncDriver]  = None


# ── PostgreSQL ────────────────────────────────────────────────────────────────

async def get_pg() -> asyncpg.Pool:
    assert _pg_pool is not None, "PostgreSQL pool not initialised"
    return _pg_pool


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so asyncpg returns Python dicts, not raw strings."""
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json",  encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def init_postgres() -> None:
    global _pg_pool
    for attempt in range(10):
        try:
            _pg_pool = await asyncpg.create_pool(dsn=DATABASE_URL, min_size=2, max_size=10, init=_init_conn)
            async with _pg_pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                        agent_id    TEXT        NOT NULL,
                        session_id  TEXT        NOT NULL,
                        type        TEXT        NOT NULL,
                        name        TEXT        NOT NULL,
                        timestamp   TIMESTAMPTZ NOT NULL,
                        metadata    JSONB       NOT NULL DEFAULT '{}',
                        parent_id   UUID        REFERENCES events(id),
                        ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_ev_agent   ON events(agent_id);
                    CREATE INDEX IF NOT EXISTS idx_ev_session ON events(session_id);
                    CREATE INDEX IF NOT EXISTS idx_ev_type    ON events(type);
                    CREATE INDEX IF NOT EXISTS idx_ev_ts      ON events(timestamp DESC);
                """)
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS tools (
                        name        TEXT PRIMARY KEY,
                        namespace   TEXT NOT NULL,
                        call_count  BIGINT NOT NULL DEFAULT 0,
                        error_count BIGINT NOT NULL DEFAULT 0,
                        agents      TEXT[] NOT NULL DEFAULT '{}',
                        first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        last_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_tools_ns ON tools(namespace);
                """)
            log.info("PostgreSQL ready")
            return
        except Exception as exc:
            log.warning("PostgreSQL not ready (attempt %d/10): %s", attempt + 1, exc)
            await asyncio.sleep(3)
    raise RuntimeError("Could not connect to PostgreSQL after 10 attempts")


async def close_postgres() -> None:
    if _pg_pool:
        await _pg_pool.close()


# ── Neo4j ─────────────────────────────────────────────────────────────────────

async def get_neo4j() -> Optional[AsyncDriver]:
    return _neo4j


async def init_neo4j() -> None:
    global _neo4j
    for attempt in range(10):
        try:
            driver = AsyncGraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASS)
            )
            async with driver.session() as s:
                await s.run("RETURN 1")   # connectivity check
                # ── Uniqueness constraints ────────────────────────────────
                for stmt in [
                    "CREATE CONSTRAINT agent_id    IF NOT EXISTS FOR (n:Agent)          REQUIRE n.agent_id    IS UNIQUE",
                    "CREATE CONSTRAINT session_id  IF NOT EXISTS FOR (n:Session)        REQUIRE n.session_id  IS UNIQUE",
                    "CREATE CONSTRAINT tool_name   IF NOT EXISTS FOR (n:Tool)           REQUIRE n.name        IS UNIQUE",
                    "CREATE CONSTRAINT llm_name    IF NOT EXISTS FOR (n:LLMModel)       REQUIRE n.name        IS UNIQUE",
                    "CREATE CONSTRAINT ext_host    IF NOT EXISTS FOR (n:ExternalSystem) REQUIRE n.host        IS UNIQUE",
                    "CREATE CONSTRAINT ns_name     IF NOT EXISTS FOR (n:Namespace)      REQUIRE n.name        IS UNIQUE",
                ]:
                    try:
                        await s.run(stmt)
                    except Exception:
                        pass  # constraint may already exist

                # ── Indexes for common traversal patterns ─────────────────
                for stmt in [
                    "CREATE INDEX event_type  IF NOT EXISTS FOR (n:Event) ON (n.type)",
                    "CREATE INDEX event_ts    IF NOT EXISTS FOR (n:Event) ON (n.timestamp)",
                    "CREATE INDEX event_agent IF NOT EXISTS FOR (n:Event) ON (n.agent_id)",
                ]:
                    try:
                        await s.run(stmt)
                    except Exception:
                        pass
            _neo4j = driver
            log.info("Neo4j ready")
            return
        except Exception as exc:
            log.warning("Neo4j not ready (attempt %d/10): %s", attempt + 1, exc)
            await asyncio.sleep(5)
    log.warning("Neo4j unavailable — graph writes will be skipped")


async def close_neo4j() -> None:
    if _neo4j:
        await _neo4j.close()
