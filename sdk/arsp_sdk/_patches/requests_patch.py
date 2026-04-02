"""
requests safety-net — wraps requests.Session.send.

Captures every outbound HTTP call not directed at the ARSP backend as a
network event. Patching Session.send is the lowest common denominator:
requests.get/post/put/etc. all go through it, as does any third-party
library built on requests.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_requests(client: "EventClient") -> None:
    try:
        import requests
        _wrap_session(client, requests)
        log.info("[arsp] requests safety-net patched")
    except ImportError:
        log.debug("[arsp] requests not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] requests patch failed: %s", exc)


def _skip(url: str, arsp_endpoint: str) -> bool:
    try:
        return url.startswith(arsp_endpoint)
    except Exception:
        return False


def _wrap_session(client: "EventClient", requests) -> None:
    original = requests.Session.send

    @functools.wraps(original)
    def patched(self, prepared, *args, **kwargs):
        url = prepared.url or ""
        if _skip(url, client.endpoint):
            return original(self, prepared, *args, **kwargs)

        t0 = time.monotonic()
        error = None
        response = None
        try:
            response = original(self, prepared, *args, **kwargs)
            return response
        except Exception as exc:
            error = exc
            raise
        finally:
            parsed = urlparse(url)
            method = prepared.method or "HTTP"
            client.send(
                type="network",
                name=f"{method} {parsed.netloc}",
                metadata={
                    "method":      method,
                    "host":        parsed.netloc,
                    "path":        parsed.path[:200],
                    "status_code": getattr(response, "status_code", None),
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "error":       str(error) if error else None,
                    "framework":   "requests",
                },
            )

    requests.Session.send = patched
