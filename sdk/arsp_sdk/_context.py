"""
Thread- and async-safe context variables for the current agent + session.
Using contextvars means each async task / thread gets its own session scope.
"""
from contextvars import ContextVar
from typing import Optional

_agent_id:   ContextVar[Optional[str]] = ContextVar("arsp_agent_id",   default=None)
_session_id: ContextVar[Optional[str]] = ContextVar("arsp_session_id", default=None)


def get_agent_id()   -> Optional[str]: return _agent_id.get()
def get_session_id() -> Optional[str]: return _session_id.get()
def set_agent_id(v: str)   -> None: _agent_id.set(v)
def set_session_id(v: str) -> None: _session_id.set(v)
