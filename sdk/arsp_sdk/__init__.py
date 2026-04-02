"""
arsp-sdk — zero-config auto-instrumentation for the ARSP Agent Observability Platform.

Quickstart
----------
import arsp_sdk as arsp

arsp.init(agent_id="my-agent", endpoint="http://localhost:8000")

# Scope events to a logical session (one per user interaction / request)
session_id = arsp.new_session()

# Context-manager form — session is restored when the block exits
with arsp.session():
    result = my_agent.run(user_input)

# Manual tracking
arsp.track("custom_event", name="do_thing", metadata={"key": "value"})
arsp.track_vector_db("vector_query", metadata={"collection": "docs", "top_k": 5})
"""
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from arsp_sdk._client import EventClient
from arsp_sdk._context import set_agent_id, set_session_id, get_session_id

__all__ = ["init", "new_session", "session", "track", "track_vector_db"]
__version__ = "0.1.0"

# Module-level client — set by init()
_client: Optional[EventClient] = None


def init(
    agent_id: str,
    endpoint: str = "http://localhost:8000",
    *,
    session_id: Optional[str] = None,
    # ── Framework patches (rich context: tools, chains, agent intent) ─────────
    patch_langchain: bool = True,
    patch_crewai:    bool = True,
    # ── Low-level SDK patches (raw model calls) ───────────────────────────────
    patch_openai:    bool = True,
    patch_gemini:    bool = True,
    patch_ollama:    bool = True,
    # ── Vector DB patches ─────────────────────────────────────────────────────
    patch_chromadb:  bool = True,
    patch_pinecone:  bool = True,
    # ── Safety-net HTTP patches (catch-all for bespoke agents) ────────────────
    patch_httpx:     bool = True,
    patch_requests:  bool = True,
) -> EventClient:
    """
    Initialise the SDK and auto-patch every installed AI framework/library.

    Patching is layered:
      1. Framework callbacks (LangChain, CrewAI) — rich context, tools, chains.
      2. Low-level SDK patches (OpenAI, Gemini, Ollama) — raw model calls for
         vanilla Python loop agents.
      3. Vector DB patches (ChromaDB, Pinecone).
      4. HTTP safety-net (httpx, requests) — catches any outbound call not
         already covered, so even completely bespoke agents leave a trace.

    All patches are no-ops when the library is not installed.
    """
    global _client

    set_agent_id(agent_id)
    set_session_id(session_id or str(uuid.uuid4()))

    _client = EventClient(endpoint=endpoint, agent_id=agent_id)

    # ── Layer 1: framework patches ────────────────────────────────────────────
    if patch_langchain:
        from arsp_sdk._patches.langchain_patch import patch_langchain as _pl
        _pl(_client)
    if patch_crewai:
        from arsp_sdk._patches.crewai_patch import patch_crewai as _pc
        _pc(_client)

    # ── Layer 2: low-level model SDK patches ──────────────────────────────────
    if patch_openai:
        from arsp_sdk._patches.openai_patch import patch_openai as _po
        _po(_client)
    if patch_gemini:
        from arsp_sdk._patches.gemini_patch import patch_gemini as _pg
        _pg(_client)
    if patch_ollama:
        from arsp_sdk._patches.ollama_patch import patch_ollama as _pol
        _pol(_client)

    # ── Layer 3: vector DB patches ────────────────────────────────────────────
    if patch_chromadb:
        from arsp_sdk._patches.chromadb_patch import patch_chromadb as _pch
        _pch(_client)
    if patch_pinecone:
        from arsp_sdk._patches.pinecone_patch import patch_pinecone as _ppi
        _ppi(_client)

    # ── Layer 4: HTTP safety-net ──────────────────────────────────────────────
    if patch_httpx:
        from arsp_sdk._patches.httpx_patch import patch_httpx as _phx
        _phx(_client)
    if patch_requests:
        from arsp_sdk._patches.requests_patch import patch_requests as _prq
        _prq(_client)

    # ── Startup diagnostic (always prints — logging may not be configured) ────
    _startup_check(_client)

    return _client


def _startup_check(client: EventClient) -> None:
    """
    Synchronously verify the backend is reachable and print a clear
    startup banner. Uses print() so it appears regardless of whether
    the host application has configured Python logging.
    """
    import httpx as _httpx

    # 1. Health check
    reachable = False
    try:
        r = _httpx.get(f"{client.endpoint}/health", timeout=3.0)
        reachable = r.status_code == 200
    except Exception as exc:
        _print_banner(client, reachable=False, error=str(exc))
        return

    if not reachable:
        _print_banner(client, reachable=False, error="non-200 from /health")
        return

    # 2. Send a real test event synchronously so we know the ingest path works
    event_id = client.send_sync(
        type="api_call",
        name="arsp_sdk_init",
        metadata={"sdk_version": __version__, "agent_id": client.agent_id},
    )
    _print_banner(client, reachable=True, event_id=event_id)


def _print_banner(
    client: EventClient,
    reachable: bool,
    error: str = "",
    event_id: Optional[str] = None,
) -> None:
    sep = "─" * 52
    print(f"\n[arsp] {sep}")
    print(f"[arsp]  ARSP SDK v{__version__}  —  agent: {client.agent_id}")
    print(f"[arsp]  endpoint: {client.endpoint}")
    if reachable:
        print(f"[arsp]  backend:  ✓ reachable")
        if event_id:
            print(f"[arsp]  test event sent — id: {event_id}")
        else:
            print(f"[arsp]  WARNING: backend reachable but event POST failed")
    else:
        print(f"[arsp]  backend:  ✗ NOT reachable  ← events will be dropped!")
        print(f"[arsp]  error:    {error}")
        print(f"[arsp]  check:    is 'docker compose up' running?")
    print(f"[arsp] {sep}\n")


def new_session(session_id: Optional[str] = None) -> str:
    """
    Start a new logical session scope and return its ID.
    All subsequent events in this thread/task will use the new session.
    """
    sid = session_id or str(uuid.uuid4())
    set_session_id(sid)
    return sid


@contextmanager
def session(session_id: Optional[str] = None) -> Iterator[str]:
    """
    Context manager that scopes all events inside the block to a new session.
    The previous session is restored when the block exits.

    Usage
    -----
    with arsp.session() as sid:
        agent.run(user_input)   # all events tagged with sid

    with arsp.session("my-fixed-id") as sid:
        ...
    """
    previous = get_session_id()
    sid = new_session(session_id)
    try:
        yield sid
    finally:
        if previous:
            set_session_id(previous)


def track(
    type: str,
    name: str,
    metadata: Optional[dict[str, Any]] = None,
    relationships: Optional[dict[str, Any]] = None,
    id: Optional[str] = None,
) -> Optional[str]:
    """
    Manually emit an event.

    Returns the server-assigned event ID (blocking call), or None if the
    SDK was not initialised or the endpoint is unreachable.
    """
    if _client is None:
        import logging
        logging.getLogger(__name__).warning(
            "[arsp] track() called before init() — event dropped"
        )
        return None
    return _client.send_sync(type=type, name=name, metadata=metadata, relationships=relationships, id=id)


def track_vector_db(
    operation: str,
    metadata: Optional[dict[str, Any]] = None,
    relationships: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """
    Track a vector database operation.
    `operation` should be one of: 'vector_insert', 'vector_query', 'vector_similarity_match', 'vector_delete'.
    """
    return track(
        type="vector_db",
        name=operation,
        metadata=metadata,
        relationships=relationships,
    )
