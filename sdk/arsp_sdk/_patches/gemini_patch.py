"""
Gemini patch — wraps google-genai (google.genai) and google-generativeai.
Captures model, prompt, response text, and token usage as llm_call events.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_gemini(client: "EventClient") -> None:
    _patched = False
    # New SDK: google-genai (google.genai.Client)
    try:
        from google.genai import models as _gm
        _wrap_genai(client, _gm)
        log.info("[arsp] google-genai patched")
        _patched = True
    except (ImportError, AttributeError):
        pass

    # Legacy SDK: google-generativeai (google.generativeai.GenerativeModel)
    try:
        import google.generativeai as genai
        _wrap_generativeai(client, genai)
        log.info("[arsp] google-generativeai patched")
        _patched = True
    except (ImportError, AttributeError):
        pass

    if not _patched:
        log.debug("[arsp] No Gemini SDK installed — skipping patch")


def _wrap_genai(client: "EventClient", models_module) -> None:
    """Patch google.genai models.generate_content (sync + async)."""
    try:
        from google.genai.models import Models, AsyncModels
    except ImportError:
        return

    original_sync = Models.generate_content

    @functools.wraps(original_sync)
    def patched_sync(self, *, model: str, contents, **kwargs):
        t0 = time.monotonic()
        result = original_sync(self, model=model, contents=contents, **kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        client.send(
            type="llm_call",
            name=model,
            metadata=_extract_genai_meta(model, contents, result, duration_ms),
        )
        return result

    Models.generate_content = patched_sync

    try:
        original_async = AsyncModels.generate_content

        @functools.wraps(original_async)
        async def patched_async(self, *, model: str, contents, **kwargs):
            t0 = time.monotonic()
            result = await original_async(self, model=model, contents=contents, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            client.send(
                type="llm_call",
                name=model,
                metadata=_extract_genai_meta(model, contents, result, duration_ms),
            )
            return result

        AsyncModels.generate_content = patched_async
    except Exception:
        pass


def _extract_genai_meta(model: str, contents, result, duration_ms: int) -> dict:
    prompt_text = ""
    if isinstance(contents, str):
        prompt_text = contents[:400]
    elif isinstance(contents, list) and contents:
        prompt_text = str(contents[-1])[:400]

    response_text = ""
    try:
        response_text = result.text[:400]
    except Exception:
        pass

    usage = {}
    try:
        um = result.usage_metadata
        usage = {
            "prompt_tokens":     um.prompt_token_count,
            "completion_tokens": um.candidates_token_count,
            "total_tokens":      um.total_token_count,
        }
    except Exception:
        pass

    return {
        "model":       model,
        "prompt":      prompt_text,
        "response":    response_text,
        "usage":       usage,
        "duration_ms": duration_ms,
        "framework":   "gemini",
    }


def _wrap_generativeai(client: "EventClient", genai) -> None:
    """Patch google.generativeai.GenerativeModel.generate_content (sync + async)."""
    try:
        from google.generativeai import GenerativeModel
    except ImportError:
        return

    original_sync = GenerativeModel.generate_content

    @functools.wraps(original_sync)
    def patched_sync(self, contents, **kwargs):
        t0 = time.monotonic()
        result = original_sync(self, contents, **kwargs)
        duration_ms = int((time.monotonic() - t0) * 1000)
        model_name = getattr(self, "model_name", "gemini")
        client.send(
            type="llm_call",
            name=model_name,
            metadata=_extract_generativeai_meta(model_name, contents, result, duration_ms),
        )
        return result

    GenerativeModel.generate_content = patched_sync

    try:
        original_async = GenerativeModel.generate_content_async

        @functools.wraps(original_async)
        async def patched_async(self, contents, **kwargs):
            t0 = time.monotonic()
            result = await original_async(self, contents, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            model_name = getattr(self, "model_name", "gemini")
            client.send(
                type="llm_call",
                name=model_name,
                metadata=_extract_generativeai_meta(model_name, contents, result, duration_ms),
            )
            return result

        GenerativeModel.generate_content_async = patched_async
    except Exception:
        pass


def _extract_generativeai_meta(model: str, contents, result, duration_ms: int) -> dict:
    prompt_text = str(contents)[:400] if contents else ""
    response_text = ""
    try:
        response_text = result.text[:400]
    except Exception:
        pass

    usage = {}
    try:
        um = result.usage_metadata
        usage = {
            "prompt_tokens":     um.prompt_token_count,
            "completion_tokens": um.candidates_token_count,
            "total_tokens":      um.total_token_count,
        }
    except Exception:
        pass

    return {
        "model":       model,
        "prompt":      prompt_text,
        "response":    response_text,
        "usage":       usage,
        "duration_ms": duration_ms,
        "framework":   "gemini",
    }
