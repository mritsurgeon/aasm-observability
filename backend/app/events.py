"""
Event Bus — Step 11
Broadcasts real-time events to connected WebSocket clients.
Stores last 500 events for replay to new connections.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket


class EventBus:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._events: list[dict[str, Any]] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        # Replay recent history to the new client
        for event in self._events[-200:]:
            try:
                await ws.send_json(event)
            except Exception:
                break

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            self._connections.remove(ws)

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        event: dict[str, Any] = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        self._events.append(event)
        if len(self._events) > 500:
            self._events = self._events[-500:]

        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def recent(self, n: int = 200) -> list[dict[str, Any]]:
        return self._events[-n:]


event_bus = EventBus()
