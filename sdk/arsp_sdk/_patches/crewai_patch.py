"""
CrewAI patch — wraps Task.execute_sync (CrewAI ≥ 0.28) and the underlying
Agent._execute_core / execute_task methods used in earlier versions.
We try both entry points so the patch works across CrewAI versions.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_crewai(client: "EventClient") -> None:
    patched_any = False
    try:
        from crewai import Task
        if hasattr(Task, "execute_sync"):
            _wrap_task_execute_sync(client, Task)
            patched_any = True
        if hasattr(Task, "execute"):
            _wrap_task_execute(client, Task)
            patched_any = True
    except ImportError:
        log.debug("[arsp] crewai not installed — skipping patch")
        return
    except Exception as exc:
        log.warning("[arsp] CrewAI Task patch failed: %s", exc)

    try:
        from crewai import Agent
        if hasattr(Agent, "execute_task"):
            _wrap_agent_execute_task(client, Agent)
            patched_any = True
    except (ImportError, Exception) as exc:
        if "ImportError" not in type(exc).__name__:
            log.warning("[arsp] CrewAI Agent patch failed: %s", exc)

    if patched_any:
        log.info("[arsp] CrewAI patched (Task + Agent)")
    else:
        log.warning("[arsp] CrewAI installed but no known execute method found")


# ── Wrappers ──────────────────────────────────────────────────────────────────

def _wrap_task_execute_sync(client: "EventClient", Task) -> None:
    original = Task.execute_sync

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        result: Any = None
        try:
            result = original(self, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            client.send(
                type="tool_call",
                name=_task_name(self),
                metadata=_build_meta(self, result, error, t0),
            )

    Task.execute_sync = patched


def _wrap_task_execute(client: "EventClient", Task) -> None:
    original = Task.execute

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        result: Any = None
        try:
            result = original(self, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            client.send(
                type="tool_call",
                name=_task_name(self),
                metadata=_build_meta(self, result, error, t0),
            )

    Task.execute = patched


def _wrap_agent_execute_task(client: "EventClient", Agent) -> None:
    original = Agent.execute_task

    @functools.wraps(original)
    def patched(self, task, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        result: Any = None
        try:
            result = original(self, task, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            task_name = getattr(task, "name", None) or getattr(task, "description", "crewai_task")
            client.send(
                type="tool_call",
                name=f"agent:{getattr(self, 'role', 'unknown')}",
                metadata={
                    "task":        str(task_name)[:120],
                    "agent_role":  str(getattr(self, "role", ""))[:120],
                    "agent_goal":  str(getattr(self, "goal", ""))[:200],
                    "output":      str(result)[:400] if result is not None else None,
                    "error":       error,
                    "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                    "framework":   "crewai",
                },
            )

    Agent.execute_task = patched


# ── Helpers ───────────────────────────────────────────────────────────────────

def _task_name(task) -> str:
    name = getattr(task, "name", None) or getattr(task, "description", None) or "crewai_task"
    return str(name)[:120]


def _build_meta(task, result: Any, error, t0: float) -> dict:
    agent = getattr(task, "agent", None)
    return {
        "task":        _task_name(task),
        "description": str(getattr(task, "description", ""))[:400],
        "agent":       str(getattr(agent, "role", "")) if agent else None,
        "expected_output": str(getattr(task, "expected_output", ""))[:200],
        "output":      str(result)[:400] if result is not None else None,
        "error":       error,
        "duration_ms": round((time.monotonic() - t0) * 1000, 2),
        "framework":   "crewai",
    }
