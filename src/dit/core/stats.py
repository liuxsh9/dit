# src/dit/core/stats.py
"""Repo-level stats aggregated from sidecar objects."""
from __future__ import annotations

import pyzstd
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_sidecar
from dit.core.runtime_metadata import get_runtime_metadata_summary
from dit.core.sidecar import sidecar_summary
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree

_ROW_SIZE_CACHE_MAX = 200_000
_ROW_SIZE_CACHE: OrderedDict[tuple[str, str], int | None] = OrderedDict()
_ROW_SIZE_IN_FLIGHT: dict[tuple[str, str], Event] = {}
_ROW_SIZE_CACHE_LOCK = Lock()


def _clear_caches() -> None:
    """Reset all module-level caches. Intended for test teardown."""
    with _ROW_SIZE_CACHE_LOCK:
        _ROW_SIZE_CACHE.clear()
        _ROW_SIZE_IN_FLIGHT.clear()


def _cached_row_size_bytes(store: ObjectStore, row_hash: str) -> int | None:
    cache_key = (str(store.root), row_hash)
    should_read = False
    with _ROW_SIZE_CACHE_LOCK:
        if cache_key in _ROW_SIZE_CACHE:
            value = _ROW_SIZE_CACHE.pop(cache_key)
            _ROW_SIZE_CACHE[cache_key] = value
            return value
        in_flight = _ROW_SIZE_IN_FLIGHT.get(cache_key)
        if in_flight is None:
            in_flight = Event()
            _ROW_SIZE_IN_FLIGHT[cache_key] = in_flight
            should_read = True

    if not should_read:
        in_flight.wait()
        with _ROW_SIZE_CACHE_LOCK:
            return _ROW_SIZE_CACHE.get(cache_key)

    value: int | None = None
    path = store._object_path("rows", row_hash)
    if not path.exists():
        value = None
    else:
        try:
            with path.open("rb") as row_file:
                value = pyzstd.get_frame_info(row_file.read(16)).decompressed_size
        except Exception:
            row_data = store.read("rows", row_hash)
            value = None if row_data is None else len(row_data)

    with _ROW_SIZE_CACHE_LOCK:
        _ROW_SIZE_CACHE[cache_key] = value
        while len(_ROW_SIZE_CACHE) > _ROW_SIZE_CACHE_MAX:
            _ROW_SIZE_CACHE.popitem(last=False)
        in_flight = _ROW_SIZE_IN_FLIGHT.pop(cache_key, None)
        if in_flight is not None:
            in_flight.set()
    return value


def repo_stats(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
    include_size: bool = True,
) -> dict:
    """Aggregate sidecar data for all manifest files in a commit.

    Returns:
    {
      "commit_hash": "abc12345...",
      "files": [
        {
          "path": "train.jsonl",
          "row_count": 1500,
          "char_count": 1500000,
          "token_estimate": 375000,
          "avg_fields": 4.2,
          "lang_distribution": {"zh": 1230, "en": 270},
          "has_sidecar": True,
        },
        ...
      ],
      "totals": {
        "file_count": 3,
        "files_with_sidecar": 3,
        "row_count": 2000,
        "char_count": 1970000,
        "token_estimate": 494000,
        "lang_distribution": {"zh": 1420, "en": 580},
      }
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    Files without a sidecar are included with has_sidecar=False. Row totals come
    from manifests, while char/token/language totals aggregate sidecars only.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_prefix = path_prefix.strip("/") if path_prefix else None

    def _matches_path_prefix(path: str) -> bool:
        if clean_prefix is None:
            return True
        return path == clean_prefix or path.startswith(f"{clean_prefix}/")

    selected_manifests = []
    row_hashes = set()
    for path, (obj_type, obj_hash, sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if not _matches_path_prefix(path):
            continue
        manifest_data = store.read("manifests", obj_hash)
        manifest = deserialize_manifest(manifest_data) if manifest_data is not None else None
        if manifest is not None:
            row_hashes.update(entry.row_hash for entry in manifest.entries)
        selected_manifests.append((path, obj_hash, sidecar_hash, manifest))

    if include_size:
        with ThreadPoolExecutor(max_workers=64) as executor:
            row_size_by_hash = dict(zip(
                row_hashes,
                executor.map(lambda row_hash: _cached_row_size_bytes(store, row_hash), row_hashes),
            ))
    else:
        row_size_by_hash = {}

    def _manifest_shape(manifest) -> tuple[int | None, int | None]:
        if manifest is None:
            return None, None
        if not include_size:
            return len(manifest.entries), None
        row_sizes = [row_size_by_hash.get(entry.row_hash) for entry in manifest.entries]
        if any(row_size is None for row_size in row_sizes):
            return len(manifest.entries), None
        return len(manifest.entries), sum(row_sizes)

    files: list[dict] = []
    for path, _obj_hash, sidecar_hash, manifest in selected_manifests:
        manifest_row_count, size_bytes = _manifest_shape(manifest)

        if sidecar_hash is None:
            if include_size:
                summary = get_runtime_metadata_summary(store, _obj_hash)
                files.append({
                    "path": path,
                    "row_count": summary["row_count"],
                    "char_count": summary["char_count"],
                    "size_bytes": size_bytes,
                    "token_estimate": summary["token_estimate"],
                    "avg_fields": summary["avg_fields"],
                    "lang_distribution": summary["lang_distribution"],
                    "has_sidecar": False,
                })
                continue
            files.append({
                "path": path,
                "row_count": manifest_row_count,
                "char_count": None,
                "size_bytes": size_bytes,
                "token_estimate": None,
                "avg_fields": None,
                "lang_distribution": None,
                "has_sidecar": False,
            })
            continue

        sc_data = store.read("sidecars", sidecar_hash)
        if sc_data is None:
            if include_size:
                summary = get_runtime_metadata_summary(store, _obj_hash)
                files.append({
                    "path": path,
                    "row_count": summary["row_count"],
                    "char_count": summary["char_count"],
                    "size_bytes": size_bytes,
                    "token_estimate": summary["token_estimate"],
                    "avg_fields": summary["avg_fields"],
                    "lang_distribution": summary["lang_distribution"],
                    "has_sidecar": False,
                })
                continue
            files.append({
                "path": path,
                "row_count": manifest_row_count,
                "char_count": None,
                "size_bytes": size_bytes,
                "token_estimate": None,
                "avg_fields": None,
                "lang_distribution": None,
                "has_sidecar": False,
            })
            continue

        sidecar = deserialize_sidecar(sc_data)
        summary = sidecar_summary(sidecar)
        files.append({
            "path": path,
            "row_count": summary["row_count"],
            "char_count": summary["char_count"],
            "size_bytes": size_bytes,
            "token_estimate": summary["token_estimate"],
            "avg_fields": summary["avg_fields"],
            "lang_distribution": summary["lang_distribution"],
            "has_sidecar": True,
        })

    # Row counts come from manifests and are available before exact byte sizes or
    # sidecar summaries. Heavier char/token/language totals still require sidecars.
    with_sidecar = [f for f in files if f["has_sidecar"]]
    with_metadata = [f for f in files if f["char_count"] is not None]
    total_lang: dict[str, int] = {}
    for f in with_metadata:
        for lang, count in (f["lang_distribution"] or {}).items():
            total_lang[lang] = total_lang.get(lang, 0) + count
    total_rows = sum(f["row_count"] for f in files if f["row_count"] is not None)

    totals: dict = {
        "file_count": len(files),
        "files_with_sidecar": len(with_sidecar),
        "row_count": total_rows,
        "char_count": sum(f["char_count"] for f in with_metadata) if with_metadata else None,
        "size_bytes": sum(f["size_bytes"] for f in files if f["size_bytes"] is not None) if include_size else None,
        "token_estimate": sum(f["token_estimate"] for f in with_metadata) if with_metadata else None,
        "lang_distribution": total_lang if with_metadata else {},
    }

    return {"commit_hash": commit_hash, "files": files, "totals": totals}


def compare_stats(
    store: ObjectStore,
    commit1: str,
    commit2: str,
    path_prefix: str | None = None,
) -> dict:
    """Compute delta between two commits' sidecar aggregates.

    Returns:
    {
      "commit1": "abc12345...",
      "commit2": "def67890...",
      "files": [
        {
          "path": "train.jsonl",
          "old": { <file entry as in repo_stats> },
          "new": { <file entry as in repo_stats> },
          "delta": {
            "row_count": 300,
            "char_count": 300000,
            "token_estimate": 75000,
          }
        },
        ...
      ],
      "totals_delta": {
        "row_count": 300,
        "char_count": 300000,
        "token_estimate": 75000,
      }
    }

    Only includes files where BOTH old and new have has_sidecar=True.
    Files present in only one commit or missing sidecar on either side are omitted.
    """
    old_result = repo_stats(store, commit1, path_prefix=path_prefix)
    new_result = repo_stats(store, commit2, path_prefix=path_prefix)

    old_by_path = {f["path"]: f for f in old_result["files"]}
    new_by_path = {f["path"]: f for f in new_result["files"]}

    all_paths = sorted(set(old_by_path) & set(new_by_path))

    files: list[dict] = []
    for path in all_paths:
        old_f = old_by_path[path]
        new_f = new_by_path[path]
        if not old_f["has_sidecar"] or not new_f["has_sidecar"]:
            continue
        delta = {
            "row_count": new_f["row_count"] - old_f["row_count"],
            "char_count": new_f["char_count"] - old_f["char_count"],
            "token_estimate": new_f["token_estimate"] - old_f["token_estimate"],
        }
        files.append({"path": path, "old": old_f, "new": new_f, "delta": delta})

    totals_delta = {
        "row_count": sum(f["delta"]["row_count"] for f in files),
        "char_count": sum(f["delta"]["char_count"] for f in files),
        "token_estimate": sum(f["delta"]["token_estimate"] for f in files),
    }

    return {
        "commit1": commit1,
        "commit2": commit2,
        "files": files,
        "totals_delta": totals_delta,
    }
