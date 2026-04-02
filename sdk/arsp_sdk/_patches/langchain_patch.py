"""
LangChain patch — injects an ARSP BaseCallbackHandler into LangChain's
global callback manager so ALL LLM calls, tool calls, and chain runs are
captured automatically, regardless of provider (OpenAI, Ollama, Gemini…).

The old BaseTool monkey-patch only caught tool._run(); this callback approach
is the pattern LangChain itself recommends for observability and covers:
  - on_llm_start / on_llm_end / on_llm_error
  - on_tool_start / on_tool_end / on_tool_error
  - on_chain_start / on_chain_end / on_chain_error
"""
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

        handler = _ARSPCallbackHandler(client)

        # Inject into the global callback manager so every chain/llm/tool
        # created after init() automatically has our handler.
        try:
            from langchain_core.callbacks import get_callback_manager
            get_callback_manager().add_handler(handler, inherit=True)
            log.info("[arsp] LangChain callback handler injected (global manager)")
            return
        except Exception:
            pass

        # Fallback: set as a global handler via the module-level registry
        try:
            import langchain_core.callbacks.manager as _mgr
            if not hasattr(_mgr, "_arsp_handler_injected"):
                _orig_init = BaseCallbackManager.__init__

                def _patched_init(self, *args, **kwargs):
                    _orig_init(self, *args, **kwargs)
                    try:
                        self.add_handler(handler, inherit=True)
                    except Exception:
                        pass

                BaseCallbackManager.__init__ = _patched_init
                _mgr._arsp_handler_injected = True
            log.info("[arsp] LangChain callback handler injected (manager __init__ patch)")
        except Exception as exc:
            log.warning("[arsp] LangChain callback injection failed: %s", exc)

    except ImportError:
        log.debug("[arsp] langchain-core not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] LangChain patch failed: %s", exc)


class _ARSPCallbackHandler:
    """
    LangChain BaseCallbackHandler that forwards events to the ARSP backend.
    Inherits lazily so the import works even if langchain-core is installed
    after the SDK — the class body only runs when patch_langchain() is called.
    """

    def __new__(cls, client: "EventClient"):
        from langchain_core.callbacks import BaseCallbackHandler

        # Build the real class the first time, then cache it
        if not hasattr(cls, "_built"):
            cls._built = type(
                "_ARSPCallbackHandlerImpl",
                (BaseCallbackHandler,),
                {
                    "__init__":        cls._init,
                    "on_llm_start":    cls._on_llm_start,
                    "on_llm_end":      cls._on_llm_end,
                    "on_llm_error":    cls._on_llm_error,
                    "on_tool_start":   cls._on_tool_start,
                    "on_tool_end":     cls._on_tool_end,
                    "on_tool_error":   cls._on_tool_error,
                    "on_chain_start":  cls._on_chain_start,
                    "on_chain_end":    cls._on_chain_end,
                    "on_chain_error":  cls._on_chain_error,
                    "raise_error":     False,
                    "ignore_llm":      False,
                    "ignore_chain":    False,
                    "ignore_agent":    False,
                },
            )
        return cls._built(client)

    # ── instance setup ────────────────────────────────────────────────────────

    @staticmethod
    def _init(self, client: "EventClient"):
        super(self.__class__, self).__init__()
        self._client = client
        self._t0: dict[str, float] = {}  # run_id -> start time

    # ── LLM events ───────────────────────────────────────────────────────────

    @staticmethod
    def _on_llm_start(self, serialized: dict, prompts: list[str],
                      *, run_id: UUID, **kwargs: Any) -> None:
        self._t0[str(run_id)] = time.monotonic()
        model = (serialized.get("kwargs") or {}).get("model_name") \
             or (serialized.get("kwargs") or {}).get("model") \
             or serialized.get("id", ["unknown"])[-1]
        self._client.send(
            type="llm_call",
            name=str(model),
            metadata={
                "framework":    "langchain",
                "prompt_count": len(prompts),
                "prompt":       prompts[0][:500] if prompts else "",
            },
            id=str(run_id),
        )

    @staticmethod
    def _on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        duration_ms = round((time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2)
        generations = getattr(response, "generations", [])
        output = ""
        if generations:
            first = generations[0]
            if first:
                output = getattr(first[0], "text", str(first[0]))[:500]
        usage = {}
        if hasattr(response, "llm_output") and response.llm_output:
            usage = response.llm_output.get("token_usage", {})
        self._client.send(
            type="llm_call",
            name="llm_response",
            metadata={
                "framework":   "langchain",
                "output":      output,
                "duration_ms": duration_ms,
                **{k: v for k, v in usage.items()},
            },
        )

    @staticmethod
    def _on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._t0.pop(str(run_id), None)
        self._client.send(
            type="llm_call",
            name="llm_error",
            metadata={"framework": "langchain", "error": str(error)},
        )

    # ── Tool events ───────────────────────────────────────────────────────────

    @staticmethod
    def _on_tool_start(self, serialized: dict, input_str: str,
                       *, run_id: UUID, **kwargs: Any) -> None:
        self._t0[str(run_id)] = time.monotonic()
        tool_name = serialized.get("name") or kwargs.get("name", "unknown_tool")
        self._client.send(
            type="tool_call",
            name=str(tool_name),
            metadata={
                "framework": "langchain",
                "input":     input_str[:400],
            },
            id=str(run_id),
        )

    @staticmethod
    def _on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        duration_ms = round((time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2)
        self._client.send(
            type="tool_call",
            name="tool_response",
            metadata={
                "framework":   "langchain",
                "output":      str(output)[:400],
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def _on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        duration_ms = round((time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2)
        self._client.send(
            type="tool_call",
            name="tool_error",
            metadata={
                "framework":   "langchain",
                "error":       str(error),
                "duration_ms": duration_ms,
            },
        )

    # ── Chain events ──────────────────────────────────────────────────────────

    @staticmethod
    def _on_chain_start(self, serialized: dict, inputs: dict,
                        *, run_id: UUID, **kwargs: Any) -> None:
        self._t0[str(run_id)] = time.monotonic()
        chain_name = serialized.get("id", ["unknown"])[-1] if serialized else "unknown"
        self._client.send(
            type="llm_call",
            name=f"chain:{chain_name}",
            metadata={
                "framework": "langchain",
                "inputs":    str(inputs)[:400],
            },
            id=str(run_id),
        )

    @staticmethod
    def _on_chain_end(self, outputs: dict, *, run_id: UUID, **kwargs: Any) -> None:
        duration_ms = round((time.monotonic() - self._t0.pop(str(run_id), time.monotonic())) * 1000, 2)
        self._client.send(
            type="llm_call",
            name="chain_end",
            metadata={
                "framework":   "langchain",
                "outputs":     str(outputs)[:400],
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def _on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._t0.pop(str(run_id), None)
        self._client.send(
            type="llm_call",
            name="chain_error",
            metadata={"framework": "langchain", "error": str(error)},
        )
