"""Runtime metadata summaries for manifests without committed sidecars."""
from __future__ import annotations

from collections import OrderedDict
from threading import Event, Lock

from dit.core.sidecar import compute_sidecar, sidecar_summary
from dit.core.store import ObjectStore

_SUMMARY_CACHE_MAX = 20_000
_SUMMARY_CACHE: OrderedDict[tuple[str, str], dict] = OrderedDict()
_SUMMARY_IN_FLIGHT: dict[tuple[str, str], Event] = {}
_SUMMARY_CACHE_LOCK = Lock()


def clear_runtime_metadata_cache() -> None:
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE.clear()
        _SUMMARY_IN_FLIGHT.clear()


def get_runtime_metadata_summary(
    store: ObjectStore,
    manifest_hash: str,
    *,
    refresh: bool = False,
) -> dict:
    """Return cached or freshly computed metadata summary for a manifest hash."""
    cache_key = (str(store.root), manifest_hash)
    should_compute = False

    with _SUMMARY_CACHE_LOCK:
        if refresh:
            _SUMMARY_CACHE.pop(cache_key, None)
        elif cache_key in _SUMMARY_CACHE:
            value = _SUMMARY_CACHE.pop(cache_key)
            _SUMMARY_CACHE[cache_key] = value
            return dict(value)

        in_flight = _SUMMARY_IN_FLIGHT.get(cache_key)
        if in_flight is None:
            in_flight = Event()
            _SUMMARY_IN_FLIGHT[cache_key] = in_flight
            should_compute = True

    if not should_compute:
        in_flight.wait()
        with _SUMMARY_CACHE_LOCK:
            cached = _SUMMARY_CACHE.get(cache_key)
        if cached is not None:
            return dict(cached)

    try:
        summary = sidecar_summary(compute_sidecar(store, manifest_hash))
    except Exception:
        with _SUMMARY_CACHE_LOCK:
            waiter = _SUMMARY_IN_FLIGHT.pop(cache_key, None)
            if waiter is not None:
                waiter.set()
        raise

    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE[cache_key] = summary
        while len(_SUMMARY_CACHE) > _SUMMARY_CACHE_MAX:
            _SUMMARY_CACHE.popitem(last=False)
        waiter = _SUMMARY_IN_FLIGHT.pop(cache_key, None)
        if waiter is not None:
            waiter.set()
    return dict(summary)
