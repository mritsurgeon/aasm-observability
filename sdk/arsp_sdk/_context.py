"""
Thread- and async-safe context variables for the current agent + session.
Using contextvars means each async task / thread gets its own session scope.

_crew_session_id is a plain module-level string (not a ContextVar) so it is
visible to all OS threads. Crew.kickoff sets it before spawning worker threads,
giving every thread a shared fallback session rather than each generating a
random UUID.
"""
from contextvars import ContextVar
from typing import Optional

_agent_id:   ContextVar[Optional[str]] = ContextVar("arsp_agent_id",   default=None)
_session_id: ContextVar[Optional[str]] = ContextVar("arsp_session_id", default=None)

# Set once per Crew.kickoff — readable by all threads without ContextVar
_crew_session_id: Optional[str] = None


def get_agent_id()   -> Optional[str]: return _agent_id.get()
def get_session_id() -> Optional[str]: return _session_id.get()
def set_agent_id(v: str)   -> None: _agent_id.set(v)
def set_session_id(v: str) -> None: _session_id.set(v)


def set_crew_session(sid: str) -> None:
    global _crew_session_id
    _crew_session_id = sid


def get_crew_session() -> Optional[str]:
    return _crew_session_id
