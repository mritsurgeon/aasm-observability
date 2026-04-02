"""
Pinecone patch — wraps pinecone.data.Index for query, upsert, delete, fetch.
Emits vector_db events, mirroring the ChromaDB patch style.

Supports pinecone-client >= 3.0 (the `pinecone.data.Index` import path).
For older 2.x clients the import path differs; we fall back gracefully.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)

_OPS = {
    "query":  "vector_query",
    "upsert": "vector_insert",
    "delete": "vector_delete",
    "fetch":  "vector_query",
    "update": "vector_insert",
}


def patch_pinecone(client: "EventClient") -> None:
    patched = False
    # v3+ path
    try:
        from pinecone.data import Index
        _wrap_index(client, Index)
        log.info("[arsp] Pinecone patched (v3+)")
        patched = True
    except (ImportError, AttributeError):
        pass

    # v2 fallback
    if not patched:
        try:
            from pinecone import Index  # type: ignore[no-redef]
            _wrap_index(client, Index)
            log.info("[arsp] Pinecone patched (v2)")
            patched = True
        except (ImportError, AttributeError):
            pass

    if not patched:
        log.debug("[arsp] pinecone not installed — skipping patch")


def _wrap_index(client: "EventClient", Index) -> None:
    for method_name, operation in _OPS.items():
        if hasattr(Index, method_name):
            _patch_method(client, Index, method_name, operation)


def _patch_method(client: "EventClient", Index, method_name: str, operation: str) -> None:
    original = getattr(Index, method_name)

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        try:
            result = original(self, *args, **kwargs)
            return result
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            meta: dict = {
                "operation":   operation,
                "method":      method_name,
                "db_type":     "pinecone",
                "duration_ms": int((time.monotonic() - t0) * 1000),
                "index":       _index_name(self),
            }
            if error:
                meta["error"] = error
            if "top_k" in kwargs:
                meta["top_k"] = kwargs["top_k"]
            if "namespace" in kwargs:
                meta["namespace"] = str(kwargs["namespace"])
            if "vectors" in kwargs:
                vecs = kwargs["vectors"]
                meta["vector_count"] = len(vecs) if isinstance(vecs, list) else 1
            if "filter" in kwargs:
                meta["filter"] = str(kwargs["filter"])[:200]
            client.send(type="vector_db", name=operation, metadata=meta)

    setattr(Index, method_name, patched)


def _index_name(index_obj) -> str:
    for attr in ("_index_name", "name", "_name", "index_name"):
        val = getattr(index_obj, attr, None)
        if val:
            return str(val)
    return "unknown"
