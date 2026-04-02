"""
ChromaDB patch — wraps Collection.add, Collection.query, Collection.get, and Collection.delete.
Emits vector_db events for each operation so ChromaDB usage is tracked automatically.
"""
import functools
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from arsp_sdk._client import EventClient

log = logging.getLogger(__name__)


def patch_chromadb(client: "EventClient") -> None:
    try:
        from chromadb.api.models.Collection import Collection
        _wrap_collection(client, Collection)
        log.info("[arsp] ChromaDB patched (add, query, get, delete)")
    except ImportError:
        log.debug("[arsp] chromadb not installed — skipping patch")
    except Exception as exc:
        log.warning("[arsp] ChromaDB patch failed: %s", exc)


def _wrap_collection(client: "EventClient", Collection) -> None:
    _patch_method(client, Collection, "add",    "vector_insert")
    _patch_method(client, Collection, "query",  "vector_query")
    _patch_method(client, Collection, "get",    "vector_query")
    _patch_method(client, Collection, "delete", "vector_delete")
    _patch_method(client, Collection, "upsert", "vector_insert")


def _patch_method(
    client: "EventClient",
    Collection,
    method_name: str,
    operation: str,
) -> None:
    original = getattr(Collection, method_name, None)
    if original is None:
        return

    @functools.wraps(original)
    def patched(self, *args, **kwargs):
        t0 = time.monotonic()
        error = None
        try:
            result = original(self, *args, **kwargs)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - t0) * 1000)
            meta = _build_meta(self, operation, method_name, kwargs, duration_ms, error)
            client.send(type="vector_db", name=operation, metadata=meta)
        return result

    setattr(Collection, method_name, patched)


def _build_meta(
    collection,
    operation: str,
    method_name: str,
    kwargs: dict,
    duration_ms: int,
    error,
) -> dict:
    collection_name = getattr(collection, "name", "unknown")
    meta: dict = {
        "operation":   operation,
        "method":      method_name,
        "collection":  collection_name,
        "db_type":     "chromadb",
        "duration_ms": duration_ms,
    }
    if error:
        meta["error"] = error

    # Capture useful kwargs without bulky embeddings
    if "n_results" in kwargs:
        meta["top_k"] = kwargs["n_results"]
    if "ids" in kwargs:
        ids = kwargs["ids"]
        meta["id_count"] = len(ids) if isinstance(ids, list) else 1
    if "documents" in kwargs:
        docs = kwargs["documents"]
        meta["document_count"] = len(docs) if isinstance(docs, list) else 1
    if "query_texts" in kwargs:
        qt = kwargs["query_texts"]
        if isinstance(qt, list) and qt:
            meta["query_preview"] = str(qt[0])[:200]
    if "where" in kwargs:
        meta["where"] = str(kwargs["where"])[:200]

    return meta
