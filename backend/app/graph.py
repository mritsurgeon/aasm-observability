"""
Graph API — Step 5
Serves pre-built Cypher query results as JSON for the React Flow UI.

GET /graph/overview          — full agent→session→tool/llm/ext graph
GET /graph/agent/{agent_id}  — subgraph for one agent
GET /graph/schema            — node/relationship type counts (schema introspection)
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.database import get_neo4j

log = logging.getLogger(__name__)
router = APIRouter(prefix="/graph", tags=["graph"])


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/overview")
async def graph_overview(limit: int = Query(200, le=500)):
    """
    Returns nodes and edges for the full platform graph.
    Nodes: Agent, Session, Tool, LLMModel, ExternalSystem, Namespace
    Edges: all relationship types
    """
    neo4j = await get_neo4j()
    if not neo4j:
        return {"nodes": [], "edges": [], "error": "Neo4j unavailable"}

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    async with neo4j.session() as s:
        # Agent → Session
        res = await s.run(
            """
            MATCH (a:Agent)-[:RUNS]->(sess:Session)
            RETURN a, sess
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["a"],    "Agent")
            _add_node(nodes, record["sess"], "Session")
            edges.append(_edge(record["a"], record["sess"], "RUNS"))

        # Session → Tool
        res = await s.run(
            """
            MATCH (sess:Session)-[:CALLS]->(t:Tool)
            RETURN sess, t
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["sess"], "Session")
            _add_node(nodes, record["t"], "Tool")
            edges.append(_edge(record["sess"], record["t"], "CALLS"))

        # Tool → Namespace
        res = await s.run(
            """
            MATCH (t:Tool)-[:IN_NAMESPACE]->(ns:Namespace)
            RETURN t, ns
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["t"],  "Tool")
            _add_node(nodes, record["ns"], "Namespace")
            edges.append(_edge(record["t"], record["ns"], "IN_NAMESPACE"))

        # Session → LLMModel
        res = await s.run(
            """
            MATCH (sess:Session)-[:CALLS]->(m:LLMModel)
            RETURN sess, m
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["sess"], "Session")
            _add_node(nodes, record["m"], "LLMModel")
            edges.append(_edge(record["sess"], record["m"], "CALLS"))

        # Session/Event → ExternalSystem
        res = await s.run(
            """
            MATCH (sess:Session)-[:CONNECTS_TO]->(ext:ExternalSystem)
            RETURN sess, ext
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["sess"],   "Session")
            _add_node(nodes, record["ext"], "ExternalSystem")
            edges.append(_edge(record["sess"], record["ext"], "CONNECTS_TO"))

        # Session → Memory
        res = await s.run(
            """
            MATCH (sess:Session)-[:WRITES]->(m:Memory)
            RETURN sess, m
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["sess"], "Session")
            _add_node(nodes, record["m"], "Memory")
            edges.append(_edge(record["sess"], record["m"], "WRITES"))

        # Session → VectorDB
        res = await s.run(
            """
            MATCH (sess:Session)-[:QUERIES]->(v:VectorDB)
            RETURN sess, v
            LIMIT $limit
            """,
            limit=limit,
        )
        async for record in res:
            _add_node(nodes, record["sess"], "Session")
            _add_node(nodes, record["v"], "VectorDB")
            edges.append(_edge(record["sess"], record["v"], "QUERIES"))

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "counts": {
            "nodes": len(nodes),
            "edges": len(edges),
        },
    }


@router.get("/agent/{agent_id}")
async def graph_agent(agent_id: str):
    """Subgraph centred on a single agent — all sessions, tools, LLMs, externals."""
    neo4j = await get_neo4j()
    if not neo4j:
        raise HTTPException(503, detail="Neo4j unavailable")

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    async with neo4j.session() as s:
        res = await s.run(
            """
            MATCH (a:Agent {agent_id: $agent_id})
            OPTIONAL MATCH (a)-[:RUNS]->(sess:Session)
            OPTIONAL MATCH (sess)-[:CALLS]->(t:Tool)-[:IN_NAMESPACE]->(ns:Namespace)
            OPTIONAL MATCH (sess)-[:CALLS]->(m:LLMModel)
            OPTIONAL MATCH (sess)-[:CONNECTS_TO]->(ext:ExternalSystem)
            OPTIONAL MATCH (sess)-[:WRITES]->(mem:Memory)
            OPTIONAL MATCH (sess)-[:QUERIES]->(v:VectorDB)
            RETURN a, sess, t, ns, m, ext, mem, v
            """,
            agent_id=agent_id,
        )

        found = False
        async for record in res:
            found = True
            _add_node(nodes, record["a"], "Agent")
            if record["sess"]:
                _add_node(nodes, record["sess"], "Session")
                edges.append(_edge(record["a"], record["sess"], "RUNS"))
            sess = record["sess"]
            if record["t"] and sess:
                _add_node(nodes, record["t"], "Tool")
                edges.append(_edge(sess, record["t"], "CALLS"))
            if record["ns"] and record["t"]:
                _add_node(nodes, record["ns"], "Namespace")
                edges.append(_edge(record["t"], record["ns"], "IN_NAMESPACE"))
            if record["m"] and sess:
                _add_node(nodes, record["m"], "LLMModel")
                edges.append(_edge(sess, record["m"], "CALLS"))
            if record["ext"] and sess:
                _add_node(nodes, record["ext"], "ExternalSystem")
                edges.append(_edge(sess, record["ext"], "CONNECTS_TO"))
            if record["mem"] and sess:
                _add_node(nodes, record["mem"], "Memory")
                edges.append(_edge(sess, record["mem"], "WRITES"))
            if record["v"] and sess:
                _add_node(nodes, record["v"], "VectorDB")
                edges.append(_edge(sess, record["v"], "QUERIES"))

        if not found:
            raise HTTPException(404, detail=f"Agent '{agent_id}' not found in graph")

    # De-duplicate edges
    seen_edges: set[tuple] = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    return {
        "nodes": list(nodes.values()),
        "edges": unique_edges,
        "counts": {"nodes": len(nodes), "edges": len(unique_edges)},
    }


@router.get("/schema")
async def graph_schema():
    """Count of each node label and relationship type — schema introspection."""
    neo4j = await get_neo4j()
    if not neo4j:
        return {"node_labels": {}, "rel_types": {}, "error": "Neo4j unavailable"}

    node_labels: dict[str, int] = {}
    rel_types:   dict[str, int] = {}

    async with neo4j.session() as s:
        res = await s.run(
            "CALL db.labels() YIELD label RETURN label"
        )
        labels = [r["label"] async for r in res]

        for label in labels:
            count_res = await s.run(f"MATCH (n:{label}) RETURN count(n) AS c")
            record = await count_res.single()
            node_labels[label] = record["c"] if record else 0

        res = await s.run(
            "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType"
        )
        rel_type_names = [r["relationshipType"] async for r in res]

        for rt in rel_type_names:
            count_res = await s.run(f"MATCH ()-[r:{rt}]->() RETURN count(r) AS c")
            record = await count_res.single()
            rel_types[rt] = record["c"] if record else 0

    return {"node_labels": node_labels, "rel_types": rel_types}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _node_id(neo4j_node) -> str:
    """Stable string ID from Neo4j element_id."""
    return str(neo4j_node.element_id)


def _node_label(neo4j_node) -> str:
    labels = list(neo4j_node.labels)
    return labels[0] if labels else "Unknown"


def _node_display(neo4j_node, label: str) -> str:
    props = dict(neo4j_node)
    return (
        props.get("agent_id")
        or props.get("session_id")
        or props.get("name")
        or props.get("host")
        or label
    )


def _add_node(nodes: dict, neo4j_node, label: str) -> None:
    if neo4j_node is None:
        return
    nid = _node_id(neo4j_node)
    if nid not in nodes:
        nodes[nid] = {
            "id":    nid,
            "label": label,
            "data":  dict(neo4j_node),
            "display": _node_display(neo4j_node, label),
        }


def _edge(source, target, rel_type: str) -> dict:
    return {
        "id":     f"{_node_id(source)}-{rel_type}-{_node_id(target)}",
        "source": _node_id(source),
        "target": _node_id(target),
        "type":   rel_type,
    }
