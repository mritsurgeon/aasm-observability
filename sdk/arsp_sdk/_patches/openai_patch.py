"""
OpenAI patch — wraps openai v1+ sync and async Completions.create.
Captures model, truncated prompts, response content, and token usage.
"""
import functools
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_openai(client: "EventClient") -> None:
    try:
        from openai.resources.chat.completions import Completions, AsyncCompletions
        _wrap_sync(client, Completions)
        _wrap_async(client, AsyncCompletions)
        log.info("[arsp] OpenAI patched (sync + async)")
    except ImportError:
        log.debug("[arsp] openai not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] OpenAI patch failed: %s", exc)


def _extract_meta(model: str, messages: list, result) -> dict:
    usage = {}
    if hasattr(result, "usage") and result.usage:
        usage = {
            "prompt_tokens":     result.usage.prompt_tokens,
            "completion_tokens": result.usage.completion_tokens,
            "total_tokens":      result.usage.total_tokens,
        }
    content = ""
    if result.choices:
        msg = result.choices[0].message
        content = (msg.content or "") if hasattr(msg, "content") else ""

    return {
        "model":    model,
        "messages": [
            {"role": m.get("role", ""), "content": str(m.get("content", ""))[:400]}
            for m in (messages[-3:] if messages else [])
        ],
        "response": content[:400],
        "usage":    usage,
        "framework": "openai",
    }


def _wrap_sync(client: "EventClient", Completions) -> None:
    original = Completions.create

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        model    = kwargs.get("model", args[0] if args else "unknown")
        messages = kwargs.get("messages", [])
        result   = original(self, *args, **kwargs)
        client.send(
            type="llm_call",
            name=model,
            metadata=_extract_meta(model, messages, result),
        )
        return result

    Completions.create = patched


def _wrap_async(client: "EventClient", AsyncCompletions) -> None:
    original = AsyncCompletions.create

    @functools.wraps(original)
    async def patched(self, *args, **kwargs):
        model    = kwargs.get("model", args[0] if args else "unknown")
        messages = kwargs.get("messages", [])
        result   = await original(self, *args, **kwargs)
        client.send(
            type="llm_call",
            name=model,
            metadata=_extract_meta(model, messages, result),
        )
        return result

    AsyncCompletions.create = patched
