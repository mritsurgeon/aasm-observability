"""
CrewAI patch — five hooks, zero customer config required.

1. Crew.kickoff / kickoff_async / kickoff_for_each
   Auto-scopes each run to a fresh ARSP session — BUT only when no session is
   already active. If the caller has already called arsp.new_session() (e.g. an
   interactive chat loop), that session is respected and all kickoffs share it.

2. CrewAI memory classes  (save + search)
   Patches ShortTermMemory, LongTermMemory, EntityMemory, UserMemory and the
   legacy UnifiedMemory wrapper. Emits type="memory" events so the ARSP memory
   panel populates correctly regardless of which class is active.

3. crewai.memory.storage.rag_storage.RAGStorage  (search + save)
   CrewAI's vector-DB adapter (wraps LanceDB or ChromaDB). Emitting
   type="vector_db" events gives visibility into semantic memory lookups and
   writes that are otherwise invisible in the tool stream.

4. crewai.tools.BaseTool.run
   Captures actual tool invocations (get_now, list_files, calculate, …).
   CrewAI tools are NOT LangChain BaseTool subclasses so the LangChain patch
   is blind to them. Fires once per tool call with name, input, output, duration.

5. Agent.execute_task
   One event per task — carries role, goal, backstory, tool list, output, and
   duration. Patching only this level avoids the double-fire that occurred when
   Task.execute_sync was also wrapped. Name uses "agent.{role}" so the
   tool registry groups all agent events under the "agent" namespace.
"""
import functools
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_crewai(client: "EventClient") -> None:
    patched: list[str] = []

    try:
        from crewai import Crew
        _wrap_crew_kickoff(Crew)
        patched.append("Crew.kickoff*")
    except ImportError:
        log.debug("[arsp] crewai not installed — skipping patch")
        return
    except Exception as exc:
        log.warning("[arsp] CrewAI Crew patch failed: %s", exc)

    mem_labels = _wrap_crewai_memory(client)
    if mem_labels:
        patched.extend(mem_labels)

    try:
        _wrap_crewai_rag_storage(client)
        patched.append("RAGStorage.search/save")
    except Exception as exc:
        log.warning("[arsp] CrewAI RAGStorage patch failed: %s", exc)

    try:
        _wrap_crewai_tools(client)
        patched.append("BaseTool.run")
    except Exception as exc:
        log.warning("[arsp] CrewAI BaseTool patch failed: %s", exc)

    try:
        from crewai import Agent
        if hasattr(Agent, "execute_task"):
            _wrap_agent_execute_task(client, Agent)
            patched.append("Agent.execute_task")
    except Exception as exc:
        log.warning("[arsp] CrewAI Agent patch failed: %s", exc)

    if patched:
        log.info("[arsp] CrewAI patched (%s)", ", ".join(patched))


# ── Session auto-scoping ──────────────────────────────────────────────────────

def _wrap_crew_kickoff(Crew) -> None:
    """
    Wraps kickoff variants to auto-create a session per run.

    Respects an existing session: if the caller already called
    arsp.new_session() (e.g. an interactive chat loop), all kickoff calls
    inside that loop share the same session. A new session is only created
    when there is no active session at kickoff time.
    """
    from arsp_sdk._context import get_session_id, set_session_id, set_crew_session

    def _ensure_session():
        """Return (previous, created_new). Only creates a session if none exists."""
        previous = get_session_id()
        if previous:
            set_crew_session(previous)   # make it visible to worker threads
            return previous, False
        sid = str(uuid.uuid4())
        set_session_id(sid)
        set_crew_session(sid)
        return None, True

    def _restore(previous, created_new):
        if created_new and previous:
            set_session_id(previous)
            set_crew_session(previous)

    if hasattr(Crew, "kickoff"):
        _orig = Crew.kickoff

        @functools.wraps(_orig)
        def kickoff(self, *args, **kwargs):
            previous, created_new = _ensure_session()
            try:
                return _orig(self, *args, **kwargs)
            finally:
                _restore(previous, created_new)

        Crew.kickoff = kickoff

    if hasattr(Crew, "kickoff_async"):
        _orig_async = Crew.kickoff_async

        @functools.wraps(_orig_async)
        async def kickoff_async(self, *args, **kwargs):
            previous, created_new = _ensure_session()
            try:
                return await _orig_async(self, *args, **kwargs)
            finally:
                _restore(previous, created_new)

        Crew.kickoff_async = kickoff_async

    if hasattr(Crew, "kickoff_for_each"):
        _orig_for_each = Crew.kickoff_for_each

        @functools.wraps(_orig_for_each)
        def kickoff_for_each(self, inputs, *args, **kwargs):
            outer_previous = get_session_id()
            results = []
            try:
                for item in inputs:
                    # Each input gets its own session
                    sid = str(uuid.uuid4())
                    set_session_id(sid)
                    set_crew_session(sid)
                    results.append(_orig_for_each(self, [item], *args, **kwargs))
            finally:
                if outer_previous:
                    set_session_id(outer_previous)
                    set_crew_session(outer_previous)
            return results

        Crew.kickoff_for_each = kickoff_for_each


# ── CrewAI memory capture ─────────────────────────────────────────────────────

def _patch_one_memory_class(cls: Any, client: "EventClient") -> bool:
    """
    Patch save() and search() on a single CrewAI memory class.
    Uses *args/**kwargs so the wrapper is compatible with any CrewAI version
    regardless of whether the method takes agent_id, score_threshold, etc.
    Returns True if anything was patched.
    """
    if getattr(cls, "_arsp_patched", False):
        return False

    class_label = cls.__name__

    orig_save = getattr(cls, "save", None)
    if orig_save:
        @functools.wraps(orig_save)
        def patched_save(self, *args, **kwargs):
            error = None
            result = None
            try:
                result = orig_save(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                try:
                    message = args[0] if args else kwargs.get("value", kwargs.get("message", ""))
                    client.send(
                        type="memory",
                        name="memory_write",
                        metadata={
                            "framework":    "crewai",
                            "memory_class": class_label,
                            "operation":    "save",
                            "content":      str(message)[:400],
                            "backend":      type(getattr(self, "storage", None)).__name__,
                            "error":        error,
                        },
                    )
                except Exception:
                    pass
        cls.save = patched_save

    orig_search = getattr(cls, "search", None)
    if orig_search:
        @functools.wraps(orig_search)
        def patched_search(self, *args, **kwargs):
            t0 = time.monotonic()
            error = None
            result = None
            try:
                result = orig_search(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                try:
                    query = args[0] if args else kwargs.get("query", "")
                    hits = len(result) if isinstance(result, list) else 0
                    client.send(
                        type="memory",
                        name="memory_search",
                        metadata={
                            "framework":    "crewai",
                            "memory_class": class_label,
                            "operation":    "search",
                            "query":        str(query)[:400],
                            "hits":         hits,
                            "duration_ms":  round((time.monotonic() - t0) * 1000, 2),
                            "backend":      type(getattr(self, "storage", None)).__name__,
                            "error":        error,
                        },
                    )
                except Exception:
                    pass
        cls.search = patched_search

    cls._arsp_patched = True
    return True


def _wrap_crewai_memory(client: "EventClient") -> list[str]:
    """
    Patch all known CrewAI memory classes so that every save() and search()
    call emits a type="memory" event regardless of which storage backend is
    in use (LanceDB, ChromaDB, SQLite, etc.).

    Tries the modern individual classes first (ShortTermMemory,
    LongTermMemory, EntityMemory, UserMemory) and falls back to the legacy
    UnifiedMemory wrapper.  Returns a list of label strings for the caller
    to add to the "patched" log.
    """
    import importlib

    # (module_path, class_name) candidates — ordered by likelihood
    candidates = [
        ("crewai.memory.short_term_memory", "ShortTermMemory"),
        ("crewai.memory.long_term_memory",  "LongTermMemory"),
        ("crewai.memory.entity_memory",     "EntityMemory"),
        ("crewai.memory.user_memory",       "UserMemory"),
        ("crewai.memory.memory",            "Memory"),        # modern unified
        ("crewai.memory.unified_memory",    "Memory"),        # legacy unified
    ]

    patched_labels: list[str] = []
    for module_path, class_name in candidates:
        try:
            mod = importlib.import_module(module_path)
            cls = getattr(mod, class_name)
            if _patch_one_memory_class(cls, client):
                patched_labels.append(f"{class_name}.save/search")
        except (ImportError, AttributeError):
            pass
        except Exception as exc:
            log.warning("[arsp] CrewAI memory patch failed for %s: %s", class_name, exc)

    if not patched_labels:
        log.debug("[arsp] No creawai memory classes found — memory patch skipped")

    return patched_labels


# ── CrewAI RAGStorage (vector DB) capture ────────────────────────────────────

def _wrap_crewai_rag_storage(client: "EventClient") -> None:
    """
    Patch crewai.memory.storage.rag_storage.RAGStorage so that every search()
    and save() call emits a type="vector_db" event.

    RAGStorage is the adapter CrewAI uses for semantic / short-term memory; it
    wraps LanceDB or ChromaDB internally.  Patching here gives visibility into
    vector lookups that are otherwise invisible in the tool stream.
    """
    try:
        from crewai.memory.storage.rag_storage import RAGStorage
    except ImportError:
        log.debug("[arsp] crewai.memory.storage.rag_storage not found — RAGStorage patch skipped")
        return

    if getattr(RAGStorage, "_arsp_patched", False):
        return

    # ── search ────────────────────────────────────────────────────────────────
    orig_search = getattr(RAGStorage, "search", None)
    if orig_search:
        @functools.wraps(orig_search)
        def patched_rag_search(self, *args, **kwargs):
            t0 = time.monotonic()
            error = None
            result = None
            try:
                result = orig_search(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                try:
                    query = args[0] if args else kwargs.get("query", "")
                    hits = len(result) if isinstance(result, list) else 0
                    client.send(
                        type="vector_db",
                        name="rag_storage.search",
                        metadata={
                            "framework":   "crewai",
                            "operation":   "search",
                            "db_type":     type(self).__name__,
                            "collection":  str(getattr(self, "type", ""))[:120],
                            "query":       str(query)[:400],
                            "hits":        hits,
                            "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                            "error":       error,
                        },
                    )
                except Exception:
                    pass
        RAGStorage.search = patched_rag_search

    # ── save ─────────────────────────────────────────────────────────────────
    orig_save = getattr(RAGStorage, "save", None)
    if orig_save:
        @functools.wraps(orig_save)
        def patched_rag_save(self, *args, **kwargs):
            error = None
            result = None
            try:
                result = orig_save(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                try:
                    value = args[0] if args else kwargs.get("value", "")
                    client.send(
                        type="vector_db",
                        name="rag_storage.save",
                        metadata={
                            "framework":  "crewai",
                            "operation":  "save",
                            "db_type":    type(self).__name__,
                            "collection": str(getattr(self, "type", ""))[:120],
                            "content":    str(value)[:400],
                            "error":      error,
                        },
                    )
                except Exception:
                    pass
        RAGStorage.save = patched_rag_save

    RAGStorage._arsp_patched = True


# ── CrewAI tool capture ───────────────────────────────────────────────────────

def _wrap_crewai_tools(client: "EventClient") -> None:
    """
    CrewAI tools (@tool decorator) inherit from crewai.tools.BaseTool, which
    is NOT a subclass of langchain_core.tools.BaseTool. Patch the public
    .run() entry point so every tool invocation is captured.
    """
    try:
        from crewai.tools import BaseTool as CrewBaseTool
    except ImportError:
        try:
            from crewai.tools.base_tool import BaseTool as CrewBaseTool  # type: ignore
        except ImportError:
            log.debug("[arsp] crewai.tools.BaseTool not found — tool-level patch skipped")
            return

    if getattr(CrewBaseTool, "_arsp_patched", False):
        return

    original_run = getattr(CrewBaseTool, "run", None)
    if original_run is None:
        log.debug("[arsp] crewai BaseTool has no .run() — tool-level patch skipped")
        return

    @functools.wraps(original_run)
    def patched_run(self, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        result: Any = None
        try:
            result = original_run(self, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            tool_input = args[0] if args else kwargs.get("tool_input", kwargs.get("input", ""))
            client.send(
                type="tool_call",
                name=getattr(self, "name", "crewai_tool"),
                metadata={
                    "tool":        getattr(self, "name", "crewai_tool"),
                    "description": str(getattr(self, "description", ""))[:200],
                    "input":       str(tool_input)[:400],
                    "output":      str(result)[:400] if result is not None else None,
                    "error":       error,
                    "duration_ms": round((time.monotonic() - t0) * 1000, 2),
                    "framework":   "crewai",
                },
            )

    CrewBaseTool.run = patched_run
    CrewBaseTool._arsp_patched = True


# ── Agent task execution ──────────────────────────────────────────────────────

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
            task_name = (
                getattr(task, "name", None)
                or getattr(task, "description", "crewai_task")
            )
            raw_tools = getattr(self, "tools", None) or []
            tool_names = [
                getattr(t, "name", None)
                or (getattr(t, "func", None) and getattr(t.func, "__name__", None))
                or str(t)
                for t in raw_tools
            ]
            client.send(
                type="tool_call",
                name=f"agent.{getattr(self, 'role', 'unknown')}",
                metadata={
                    "task":            str(task_name)[:120],
                    "agent_role":      str(getattr(self, "role",      ""))[:120],
                    "agent_goal":      str(getattr(self, "goal",      ""))[:200],
                    "agent_backstory": str(getattr(self, "backstory", ""))[:200],
                    "tools":           tool_names,
                    "output":          str(result)[:400] if result is not None else None,
                    "error":           error,
                    "duration_ms":     round((time.monotonic() - t0) * 1000, 2),
                    "framework":       "crewai",
                },
            )

    Agent.execute_task = patched
