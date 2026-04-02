"""
Ollama patch — wraps ollama.chat and ollama.generate (sync + async).
Captures model, prompt, response, and token usage as llm_call events.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_ollama(client: "EventClient") -> None:
    try:
        import ollama
        _wrap_chat(client, ollama)
        _wrap_generate(client, ollama)
        log.info("[arsp] Ollama patched (chat + generate, sync + async)")
    except ImportError:
        log.debug("[arsp] ollama not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] Ollama patch failed: %s", exc)


def _wrap_chat(client: "EventClient", ollama) -> None:
    original = ollama.chat

    @functools.wraps(original)
    def patched_chat(model: str, messages=None, **kwargs):
        t0 = time.monotonic()
        result = original(model=model, messages=messages, **kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        client.send(
            type="llm_call",
            name=model,
            metadata=_extract_chat_meta(model, messages or [], result, duration_ms),
        )
        return result

    ollama.chat = patched_chat

    # Async client
    try:
        from ollama import AsyncClient

        original_async = AsyncClient.chat

        @functools.wraps(original_async)
        async def patched_async_chat(self, model: str, messages=None, **kwargs):
            t0 = time.monotonic()
            result = await original_async(self, model=model, messages=messages, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            client.send(
                type="llm_call",
                name=model,
                metadata=_extract_chat_meta(model, messages or [], result, duration_ms),
            )
            return result

        AsyncClient.chat = patched_async_chat
    except (ImportError, AttributeError):
        pass


def _wrap_generate(client: "EventClient", ollama) -> None:
    original = ollama.generate

    @functools.wraps(original)
    def patched_generate(model: str, prompt: str = "", **kwargs):
        t0 = time.monotonic()
        result = original(model=model, prompt=prompt, **kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        client.send(
            type="llm_call",
            name=model,
            metadata=_extract_generate_meta(model, prompt, result, duration_ms),
        )
        return result

    ollama.generate = patched_generate

    try:
        from ollama import AsyncClient

        original_async = AsyncClient.generate

        @functools.wraps(original_async)
        async def patched_async_generate(self, model: str, prompt: str = "", **kwargs):
            t0 = time.monotonic()
            result = await original_async(self, model=model, prompt=prompt, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            client.send(
                type="llm_call",
                name=model,
                metadata=_extract_generate_meta(model, prompt, result, duration_ms),
            )
            return result

        AsyncClient.generate = patched_async_generate
    except (ImportError, AttributeError):
        pass


def _extract_chat_meta(model: str, messages: list, result, duration_ms: int) -> dict:
    response_text = ""
    try:
        response_text = result.message.content[:400]
    except Exception:
        try:
            response_text = str(result.get("message", {}).get("content", ""))[:400]
        except Exception:
            pass

    usage = {}
    try:
        usage = {
            "prompt_tokens":     result.prompt_eval_count,
            "completion_tokens": result.eval_count,
            "total_tokens":      (result.prompt_eval_count or 0) + (result.eval_count or 0),
        }
    except Exception:
        pass

    return {
        "model": model,
        "messages": [
            {"role": m.get("role", ""), "content": str(m.get("content", ""))[:400]}
            for m in (messages[-3:] if messages else [])
        ],
        "response":    response_text,
        "usage":       usage,
        "duration_ms": duration_ms,
        "framework":   "ollama",
    }


def _extract_generate_meta(model: str, prompt: str, result, duration_ms: int) -> dict:
    response_text = ""
    try:
        response_text = result.response[:400]
    except Exception:
        try:
            response_text = str(result.get("response", ""))[:400]
        except Exception:
            pass

    usage = {}
    try:
        usage = {
            "prompt_tokens":     result.prompt_eval_count,
            "completion_tokens": result.eval_count,
            "total_tokens":      (result.prompt_eval_count or 0) + (result.eval_count or 0),
        }
    except Exception:
        pass

    return {
        "model":       model,
        "prompt":      prompt[:400],
        "response":    response_text,
        "usage":       usage,
        "duration_ms": duration_ms,
        "framework":   "ollama",
    }
