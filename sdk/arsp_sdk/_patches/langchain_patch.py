"""
LangChain patch — two complementary layers so no event is ever missed:

Layer A – BaseCallbackManager injection
  Injects an ARSPCallbackHandler into every CallbackManager created after
  init(), giving rich context (model name, token usage, chain topology) for
  all LLM and chain invocations that go through LangChain's runnable system.
  Fires on_chat_model_start (chat models) AND on_llm_start (completion models).

Layer B – BaseTool._run / _arun direct patch
  Catches tool calls that happen outside a chain/agent context — e.g. when
  the developer calls tool.invoke(...) directly without passing a config.
  In those cases LangChain never creates a CallbackManager, so Layer A is
  blind to the call. This patch is the safety net.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_langchain(client: "EventClient") -> None:
    try:
        from langchain_core.callbacks.manager import BaseCallbackManager
    except ImportError:
        log.debug("[arsp] langchain-core not installed — skipping patch")
        return
    except Exception as exc:
        log.warning("[arsp] LangChain patch failed: %s", exc)
        return

    # ── Layer A: callback handler injection ───────────────────────────────────
    try:
        handler = _build_handler(client)

        import langchain_core.callbacks.manager as _mgr
        if not hasattr(_mgr, "_arsp_injected"):
            _orig_init = BaseCallbackManager.__init__

            def _patched_init(self, *args, **kwargs):
                _orig_init(self, *args, **kwargs)
                try:
                    if handler not in self.handlers:
                        self.add_handler(handler, inherit=True)
                except Exception:
                    pass

            BaseCallbackManager.__init__ = _patched_init
            _mgr._arsp_injected = True
        log.info("[arsp] LangChain callback handler injected")
    except Exception as exc:
        log.warning("[arsp] LangChain callback injection failed: %s", exc)

    # ── Layer B: direct BaseTool patch (catches tool.invoke() without config) ─
    try:
        from langchain_core.tools import BaseTool
        _wrap_tool_run(client, BaseTool)
        _wrap_tool_arun(client, BaseTool)
        log.info("[arsp] LangChain BaseTool patched (direct tool.invoke guard)")
    except ImportError:
        pass
    except Exception as exc:
        log.warning("[arsp] LangChain BaseTool patch failed: %s", exc)

    # ── Layer C: InMemoryChatMessageHistory patch ──────────────────────────────
    # InMemoryChatMessageHistory is a plain Python class — it never fires
    # callbacks, so Layer A is blind to every message added to it.
    # Patch add_message / add_messages directly.
    try:
        from langchain_core.chat_history import InMemoryChatMessageHistory
        _wrap_chat_history(client, InMemoryChatMessageHistory)
        log.info("[arsp] InMemoryChatMessageHistory patched")
    except ImportError:
        pass
    except Exception as exc:
        log.warning("[arsp] InMemoryChatMessageHistory patch failed: %s", exc)


# ── Callback handler ──────────────────────────────────────────────────────────

def _build_handler(client: "EventClient"):
    """
    Build a proper BaseCallbackHandler subclass at call-time so the import
    of BaseCallbackHandler is deferred until langchain-core is available.
    Defined as a nested class inside a function to keep the module-level
    namespace clean and avoid import-time failures.
    """
    from langchain_core.callbacks import BaseCallbackHandler

    class ARSPCallbackHandler(BaseCallbackHandler):
        raise_error = False
        ignore_llm = False
        ignore_chain = False
        ignore_agent = False

        def __init__(self):
            super().__init__()
            self._t0: dict[str, float] = {}

        # Chat models (ChatOpenAI, ChatGoogleGenerativeAI, ChatOllama, …)
        def on_chat_model_start(
            self,
            serialized: dict,
            messages: list,
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            self._t0[str(run_id)] = time.monotonic()
            model = (
                (serialized.get("kwargs") or {}).get("model")
                or (serialized.get("kwargs") or {}).get("model_name")
                or (serialized.get("id") or ["unknown"])[-1]
            )
            # Flatten the last human message for a useful preview
            prompt_preview = ""
            try:
                last_batch = messages[-1] if messages else []
                if last_batch:
                    last_msg = last_batch[-1]
                    prompt_preview = (
                        getattr(last_msg, "content", None)
                        or str(last_msg)
                    )[:400]
            except Exception:
                pass
            client.send(
                type="llm_call",
                name=str(model),
                metadata={
                    "framework":    "langchain",
                    "model":        str(model),
                    "prompt":       prompt_preview,
                    "message_count": sum(len(b) for b in messages),
                },
                id=str(run_id),
            )

        # Completion models (OpenAI text, etc.)
        def on_llm_start(
            self,
            serialized: dict,
            prompts: list[str],
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            self._t0[str(run_id)] = time.monotonic()
            model = (
                (serialized.get("kwargs") or {}).get("model_name")
                or (serialized.get("kwargs") or {}).get("model")
                or (serialized.get("id") or ["unknown"])[-1]
            )
            client.send(
                type="llm_call",
                name=str(model),
                metadata={
                    "framework":    "langchain",
                    "model":        str(model),
                    "prompt":       prompts[0][:400] if prompts else "",
                    "prompt_count": len(prompts),
                },
                id=str(run_id),
            )

        def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
            duration_ms = round(
                (time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2
            )
            output = ""
            try:
                g = response.generations
                if g and g[0]:
                    output = getattr(g[0][0], "text", None) or str(g[0][0])
                    output = output[:400]
            except Exception:
                pass
            usage = {}
            try:
                if response.llm_output:
                    usage = response.llm_output.get("token_usage") or {}
            except Exception:
                pass
            client.send(
                type="llm_call",
                name="llm_response",
                metadata={
                    "framework":   "langchain",
                    "output":      output,
                    "duration_ms": duration_ms,
                    **{k: v for k, v in usage.items()},
                },
            )

        def on_llm_error(
            self, error: BaseException, *, run_id: UUID, **kwargs: Any
        ) -> None:
            self._t0.pop(str(run_id), None)
            client.send(
                type="llm_call",
                name="llm_error",
                metadata={"framework": "langchain", "error": str(error)},
            )

        def on_tool_start(
            self,
            serialized: dict,
            input_str: str,
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            self._t0[str(run_id)] = time.monotonic()
            tool_name = serialized.get("name") or kwargs.get("name", "unknown_tool")
            client.send(
                type="tool_call",
                name=str(tool_name),
                metadata={"framework": "langchain", "input": input_str[:400]},
                id=str(run_id),
            )

        def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
            duration_ms = round(
                (time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2
            )
            client.send(
                type="tool_call",
                name="tool_response",
                metadata={
                    "framework":   "langchain",
                    "output":      str(output)[:400],
                    "duration_ms": duration_ms,
                },
            )

        def on_tool_error(
            self, error: BaseException, *, run_id: UUID, **kwargs: Any
        ) -> None:
            duration_ms = round(
                (time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2
            )
            client.send(
                type="tool_call",
                name="tool_error",
                metadata={
                    "framework":   "langchain",
                    "error":       str(error),
                    "duration_ms": duration_ms,
                },
            )

        def on_chain_start(
            self,
            serialized: dict,
            inputs: dict,
            *,
            run_id: UUID,
            **kwargs: Any,
        ) -> None:
            self._t0[str(run_id)] = time.monotonic()
            chain_name = (serialized.get("id") or ["unknown"])[-1] if serialized else "unknown"
            client.send(
                type="llm_call",
                name=f"chain:{chain_name}",
                metadata={"framework": "langchain", "inputs": str(inputs)[:400]},
                id=str(run_id),
            )

        def on_chain_end(self, outputs: dict, *, run_id: UUID, **kwargs: Any) -> None:
            duration_ms = round(
                (time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2
            )
            client.send(
                type="llm_call",
                name="chain_end",
                metadata={
                    "framework":   "langchain",
                    "outputs":     str(outputs)[:400],
                    "duration_ms": duration_ms,
                },
            )

        def on_chain_error(
            self, error: BaseException, *, run_id: UUID, **kwargs: Any
        ) -> None:
            self._t0.pop(str(run_id), None)
            client.send(
                type="llm_call",
                name="chain_error",
                metadata={"framework": "langchain", "error": str(error)},
            )

    return ARSPCallbackHandler()


# ── Layer B: direct BaseTool wrappers ─────────────────────────────────────────

def _wrap_tool_run(client: "EventClient", BaseTool) -> None:
    """
    Guard for synchronous tool.invoke() / tool.run() calls made outside
    any chain context (no callbacks= passed). Skips if a callback-based
    event was already emitted for this invocation to avoid duplicates.
    """
    original = BaseTool._run

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        # If we're inside a LangChain run context, the callback handler already
        # fired on_tool_start. Detect that via a per-invocation flag so we
        # don't double-count.
        if getattr(self, "_arsp_cb_active", False):
            return original(self, *args, **kwargs)

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
                name=getattr(self, "name", "unknown_tool"),
                metadata=_tool_meta(self, args, kwargs, result, error, t0),
            )

    BaseTool._run = patched


def _wrap_tool_arun(client: "EventClient", BaseTool) -> None:
    original = BaseTool._arun

    @functools.wraps(original)
    async def patched(self, *args, **kwargs):
        if getattr(self, "_arsp_cb_active", False):
            return await original(self, *args, **kwargs)

        t0 = time.monotonic()
        error = None
        result: Any = None
        try:
            result = await original(self, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            client.send(
                type="tool_call",
                name=getattr(self, "name", "unknown_tool"),
                metadata=_tool_meta(self, args, kwargs, result, error, t0),
            )

    BaseTool._arun = patched


def _wrap_chat_history(client: "EventClient", cls) -> None:
    """
    Patch InMemoryChatMessageHistory so every message written to RAM is
    captured as a 'memory' event.  Patches add_message (single) and
    add_messages (batch, used by RunnableWithMessageHistory).
    """
    if getattr(cls, "_arsp_patched", False):
        return

    orig_add = cls.add_message
    orig_add_many = getattr(cls, "add_messages", None)

    @functools.wraps(orig_add)
    def patched_add(self, message: Any) -> None:
        orig_add(self, message)
        try:
            client.send(
                type="memory",
                name="chat_message",
                metadata={
                    "framework": "langchain",
                    "source":    "InMemoryChatMessageHistory",
                    "role":      getattr(message, "type", "unknown"),
                    "content":   str(getattr(message, "content", message))[:400],
                    "history_length": len(getattr(self, "messages", [])),
                },
            )
        except Exception:
            pass

    cls.add_message = patched_add

    if orig_add_many is not None:
        @functools.wraps(orig_add_many)
        def patched_add_many(self, messages: Any) -> None:
            orig_add_many(self, messages)
            try:
                msgs = list(messages)
                for message in msgs:
                    client.send(
                        type="memory",
                        name="chat_message",
                        metadata={
                            "framework": "langchain",
                            "source":    "InMemoryChatMessageHistory",
                            "role":      getattr(message, "type", "unknown"),
                            "content":   str(getattr(message, "content", message))[:400],
                            "history_length": len(getattr(self, "messages", [])),
                        },
                    )
            except Exception:
                pass

        cls.add_messages = patched_add_many

    cls._arsp_patched = True


def _tool_meta(tool, args, kwargs, result: Any, error, t0: float) -> dict:
    tool_input = kwargs.get("tool_input") or (args[0] if args else "")
    return {
        "tool":        getattr(tool, "name", "unknown"),
        "description": str(getattr(tool, "description", ""))[:200],
        "input":       str(tool_input)[:400],
        "output":      str(result)[:400] if result is not None else None,
        "error":       error,
        "duration_ms": round((time.monotonic() - t0) * 1000, 2),
        "framework":   "langchain",
    }
