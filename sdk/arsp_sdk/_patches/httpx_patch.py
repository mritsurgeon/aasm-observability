"""
httpx safety-net — wraps httpx.Client.send and httpx.AsyncClient.send.

Every outbound HTTP call that is NOT directed at the ARSP backend is emitted
as a network event, giving baseline observability to vanilla Python agents
and any bespoke framework that ultimately uses httpx for external calls.

Patching at Client.send (not httpx.get/post) catches all usage styles:
raw httpx.get(), a manually constructed Client, or a library that builds
its own Client (e.g. the OpenAI SDK uses httpx internally, but those calls
are already intercepted by openai_patch — the ARSP-endpoint guard below
prevents double-counting for any SDK calls that route through this net too).
"""
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_httpx(client: "EventClient") -> None:
    try:
        import httpx
        _wrap_sync(client, httpx)
        _wrap_async(client, httpx)
        log.info("[arsp] httpx safety-net patched (sync + async)")
    except ImportError:
        log.debug("[arsp] httpx not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] httpx patch failed: %s", exc)


def _skip(url: str, arsp_endpoint: str) -> bool:
    """Guard against infinite loops: never instrument calls to the ARSP backend."""
    try:
        return url.startswith(arsp_endpoint)
    except Exception:
        return False


def _meta(request, response, duration_ms: int, error=None) -> dict:
    return {
        "method":      request.method,
        "host":        request.url.host,
        "path":        str(request.url.path)[:200],
        "status_code": getattr(response, "status_code", None),
        "duration_ms": duration_ms,
        "error":       str(error) if error else None,
        "framework":   "httpx",
    }


def _wrap_sync(client: "EventClient", httpx) -> None:
    original = httpx.Client.send

    @functools.wraps(original)
    def patched(self, request, *args, **kwargs):
        if _skip(str(request.url), client.endpoint):
            return original(self, request, *args, **kwargs)
        t0 = time.monotonic()
        error = None
        response = None
        try:
            response = original(self, request, *args, **kwargs)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            client.send(
                type="network",
                name=f"{request.method} {request.url.host}",
                metadata=_meta(request, response, int((time.monotonic() - t0) * 1000), error),
            )

    httpx.Client.send = patched


def _wrap_async(client: "EventClient", httpx) -> None:
    original = httpx.AsyncClient.send

    @functools.wraps(original)
    async def patched(self, request, *args, **kwargs):
        if _skip(str(request.url), client.endpoint):
            return await original(self, request, *args, **kwargs)
        t0 = time.monotonic()
        error = None
        response = None
        try:
            response = await original(self, request, *args, **kwargs)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            client.send(
                type="network",
                name=f"{request.method} {request.url.host}",
                metadata=_meta(request, response, int((time.monotonic() - t0) * 1000), error),
            )

    httpx.AsyncClient.send = patched
