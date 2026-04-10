"""
EventClient — non-blocking background sender.
Events are queued in-process and flushed in batches by a daemon thread.
If the ARSP endpoint is unreachable, events are silently dropped (observability
must never break the agent under observation).
"""
import atexit
import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from arsp_sdk._context import get_agent_id, get_session_id, get_crew_session

_STOP = object()  # sentinel to drain and stop the worker


class EventClient:
    def __init__(self, endpoint: str, agent_id: str) -> None:
        self.endpoint   = endpoint.rstrip("/")
        self.agent_id   = agent_id
        # Stable fallback session used when a worker thread has no ContextVar
        # session set (CrewAI spawns OS threads that don't inherit ContextVars).
        # This keeps orphaned-thread events in one session rather than creating
        # a new random session per event.
        self._default_session_id = str(uuid.uuid4())
        self._q: queue.Queue = queue.Queue(maxsize=50_000)
        self._thread = threading.Thread(
            target=self._worker, daemon=True, name="arsp-sender"
        )
        self._thread.start()
        # Fix 1: register shutdown hook so queued events flush on normal exit
        atexit.register(self._shutdown)

    # ── Public API ────────────────────────────────────────────────────────────

    def send(
        self,
        type: str,
        name: str,
        metadata: Optional[dict[str, Any]] = None,
        relationships: Optional[dict[str, Any]] = None,
        id: Optional[str] = None,
    ) -> str:
        """Queue an event for async delivery. Returns a local correlation id.

        Fix 3: _build() is called here, in the caller's thread, so ContextVar
        lookups for agent_id and session_id resolve correctly before the
        payload is handed off to the background worker.
        """
        local_id = id or str(uuid.uuid4())
        payload = self._build(type, name, metadata or {}, relationships, local_id)
        try:
            self._q.put_nowait(payload)
        except queue.Full:
            pass  # drop under extreme load
        return local_id

    def send_sync(
        self,
        type: str,
        name: str,
        metadata: Optional[dict[str, Any]] = None,
        relationships: Optional[dict[str, Any]] = None,
        id: Optional[str] = None,
    ) -> Optional[str]:
        """Synchronous send — use for manual `arsp.track()` calls."""
        payload = self._build(type, name, metadata or {}, relationships, id)
        try:
            r = httpx.post(
                f"{self.endpoint}/events",
                json=payload,
                timeout=5.0,
            )
            return r.json().get("id")
        except Exception:
            return None

    # ── Internals ─────────────────────────────────────────────────────────────

    def _build(
        self,
        type: str,
        name: str,
        metadata: dict[str, Any],
        relationships: Optional[dict[str, Any]],
        id: Optional[str],
    ) -> dict[str, Any]:
        # Fix 3: called in the caller's thread — ContextVars are valid here
        return {
            "id":         id,
            "agent_id":   get_agent_id()   or self.agent_id,
            "session_id": get_session_id() or get_crew_session() or self._default_session_id,
            "type":       type,
            "name":       name,
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "metadata":   metadata,
            "relationships": relationships,
        }

    def _shutdown(self) -> None:
        """Fix 1: drain all remaining queued events before the process exits."""
        self._q.put(_STOP)
        self._thread.join(timeout=10)

    def _worker(self) -> None:
        """Drain the queue in batches of up to 50 events every 0.5 s."""
        while True:
            batch: list[dict] = []
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:
                continue

            if item is _STOP:
                # Drain whatever is left, then exit
                while True:
                    try:
                        item = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if item is not _STOP:
                        batch.append(item)
                if batch:
                    self._flush(batch)
                return

            batch.append(item)
            while len(batch) < 50:
                try:
                    item = self._q.get_nowait()
                    if item is _STOP:
                        # flush what we have, then stop
                        self._flush(batch)
                        return
                    batch.append(item)
                except queue.Empty:
                    break

            self._flush(batch)

    def _flush(self, batch: list[dict]) -> None:
        try:
            if len(batch) == 1:
                httpx.post(
                    f"{self.endpoint}/events",
                    json=batch[0],
                    timeout=5.0,
                )
            else:
                httpx.post(
                    f"{self.endpoint}/events/batch",
                    json=batch,
                    timeout=10.0,
                )
        except Exception:
            pass  # best-effort
