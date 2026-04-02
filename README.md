# AASM — AI Agent Security Monitoring

A graph-native observability and security platform for AI agents. Zero-config telemetry capture for OpenAI, LangChain, and CrewAI workflows — visualised as a live Neo4j relationship graph, D3 timeline, risk heatmap, and tool registry.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│           Your AI Agent (OpenAI / LangChain / CrewAI)           │
│  pip install ./sdk  →  arsp.init()  →  auto-patched             │
└────────────────────────────────┬────────────────────────────────┘
                                 │  POST /events  (JSON over HTTP)
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                  FastAPI Backend  (port 8000)                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐    │
│  │ /ingest  │  │ /tools   │  │ /graph   │  │ /timeline     │    │
│  │ /events  │  │ /risk    │  │ /heatmap │  │ /health       │    │
│  └──────────┘  └──────────┘  └──────────┘  └───────────────┘    │
│       │                │                                        │
│  ┌────▼─────┐      ┌───▼────────────────────────────────────┐    │
│  │PostgreSQL│      │ Neo4j                                  │    │
│  │ events   │      │ Agent→Session→Tool/LLM/Memory/VectorDB │    │
│  │ tools    │      │ ExternalSystem/Namespace nodes         │    │
│  └──────────┘      └────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                                 │  REST + WebSocket
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Next.js Dashboard  (port 3000)                 │
│  Overview · Graph · Tools · Timeline · Heatmap · Risk · Memory  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Launch the platform

```bash
docker-compose up --build -d
```

Dashboard: `http://localhost:3000`
API: `http://localhost:8000`
Neo4j Browser: `http://localhost:7474` (neo4j / password)

### 2. Instrument your agent

```bash
pip install ./sdk
```

```python
import arsp_sdk as arsp

arsp.init(
    agent_id="my_agent",
    endpoint="http://localhost:8000",
)

# Everything below is auto-instrumented — no further changes needed
```

OpenAI, LangChain, and CrewAI calls are captured automatically from this point forward.

---

## SDK Auto-Instrumentation

| Framework / SDK | What is patched | Event type emitted |
|-----------------|----------------|--------------------|
| **OpenAI** | `Completions.create` | `llm_call` |
| **Gemini** | `GenerativeModel.generate_content` | `llm_call` |
| **Ollama** | `chat` / `generate` | `llm_call` |
| **LangChain** | `BaseCallbackHandler` (Every LLM/Tool/Chain) | `llm_call`, `tool_call` |
| **CrewAI** | `Task.execute` / `Agent.execute_task` | `tool_call` |
| **ChromaDB** | `Collection` (add, query, get, delete) | `vector_db` |
| **Pinecone** | `Index` (query, upsert, delete, fetch) | `vector_db` |
| **HTTPX** | `Client.send` / `AsyncClient.send` | `network` |
| **Requests** | `Session.send` | `network` |
| **Manual** | `arsp.track(...)` or `track_vector_db` | any |

### Manual tracking

```python
# Track any custom event
arsp.track(
    type="api_call",
    name="stripe_charge",
    metadata={"amount": 4200, "currency": "usd"},
)

# Track vector DB operations
arsp.track_vector_db(
    operation="similarity_search",
    metadata={"collection": "docs", "top_k": 5},
)
```

---

## Event Schema

All events are sent as `POST /events`:

```json
{
  "type": "tool_call",
  "name": "web_search",
  "agent_id": "research_bot",
  "session_id": "sess_abc123",
  "timestamp": "2025-06-01T14:00:00Z",
  "metadata": {
    "input": "latest AI papers",
    "duration_ms": 342,
    "error": null
  }
}
```

Supported types: `tool_call`, `llm_call`, `memory`, `api_call`, `network`, `vector_db`

---

## Dashboard Tabs

| Tab | Description |
|-----|-------------|
| **Overview** | Live event stream via WebSocket, KPI cards (total events, active agents, tool calls, error rate) |
| **Graph** | React Flow graph of the full agent→session→tool/LLM/memory/external topology |
| **Tools** | Namespace-grouped tool registry with call counts, error rates, and per-tool detail |
| **Timeline** | D3 swim-lane view — sessions as rows, events as dots on a true time axis with parent→child connectors |
| **Heatmap** | D3 color grid — 6 event types × N time buckets, coloured by risk score |
| **Risk** | Per-session and per-agent risk scores with insight classification and reasoning bullets |
| **Memory** | Raw memory event log for agents that use persistent memory |

---

## Risk Engine

Risk scores are computed per session from real event data. Score is additive and clamped to [0, 1]:

| Factor | Score added | Trigger |
|--------|-------------|---------|
| External contact | +0.12 per event | `api_call` or `network` event |
| Error rate | error_rate × 0.30 | any event with `metadata.error` set |
| High volume | +0.15 | > 30 events in session |
| Namespace spread | +0.10 | > 3 distinct tool namespaces |
| LLM usage | +0.08 | any `llm_call` event |
| Memory writes | +0.05 | any `memory` event |
| Off-hours | +0.05 | event outside 06:00–22:00 UTC |

Insight classification:

- `normal` — score < 0.40
- `elevated_risk` — score 0.40–0.69
- `critical_risk` — score ≥ 0.70

---

## Development

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run tests:
```bash
pytest tests/ -v
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### SDK

```bash
cd sdk
pip install -e ".[dev]"
pytest tests/ -v
```

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://...` | asyncpg PostgreSQL connection string |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j Bolt URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `password` | Neo4j password |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend URL for the Next.js frontend |
| `ARSP_ENDPOINT` | `http://localhost:8000` | SDK default endpoint (overridden by `arsp.init`) |

---

## Project Structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, lifespan, router registration
│   │   ├── database.py      # PostgreSQL + Neo4j connection and schema init
│   │   ├── ingest.py        # POST /events — dual-write to PG + Neo4j
│   │   ├── tools.py         # GET /tools/registry, /tools/namespaces
│   │   ├── graph.py         # GET /graph/overview, /graph/agent, /graph/schema
│   │   ├── timeline.py      # GET /timeline, /timeline/{session_id}
│   │   ├── risk.py          # GET /risk/sessions, /risk/agents
│   │   └── heatmap.py       # GET /heatmap
│   └── tests/
├── frontend/
│   ├── app/
│   │   └── page.tsx         # Dashboard shell, tab routing, WebSocket hook
│   ├── components/
│   │   ├── AgentGraph.tsx   # React Flow graph visualisation
│   │   ├── SequenceTimeline.tsx  # D3 swim-lane timeline
│   │   └── RiskHeatmap.tsx  # D3 heatmap
│   └── lib/
│       └── api.ts           # Typed REST client + all TypeScript interfaces
└── sdk/
    ├── arsp_sdk/
    │   ├── __init__.py      # Public API: init, new_session, track, track_vector_db
    │   ├── client.py        # EventClient — background queue + HTTP sender
    │   ├── context.py       # Thread-local session context
    │   └── _patches/        # openai_patch, langchain_patch, crewai_patch
    └── tests/
```
