"""Blame: trace each row in a file to the commit that introduced it."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from dit.core.diff import diff_manifests
from dit.core.objects import (
    Manifest, deserialize_commit, deserialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


@dataclass(frozen=True)
class BlameEntry:
    row_index: int
    row_hash: str
    commit_hash: str
    author: str
    timestamp: int
    query_fingerprint: Optional[str]


def _get_manifest_for_file(
    store: ObjectStore, commit_hash: str, file_path: str,
) -> tuple[Manifest, str]:
    """Load a commit's manifest for a given file path.

    Returns (manifest, manifest_hash).
    Raises FileNotFoundError if commit or file not found.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found")
    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)
    clean = file_path.lstrip("/")
    if clean not in flat:
        raise FileNotFoundError(f"File '{clean}' not found in commit {commit_hash[:8]}")
    obj_type, obj_hash, _sidecar = flat[clean]
    if obj_type != "manifest":
        raise FileNotFoundError(f"'{clean}' is not a manifest (type={obj_type})")
    m_data = store.read("manifests", obj_hash)
    if m_data is None:
        raise FileNotFoundError(f"Manifest object {obj_hash[:8]} missing from store")
    return deserialize_manifest(m_data), obj_hash


def _try_get_manifest(
    store: ObjectStore, commit_hash: str, file_path: str,
) -> Manifest | None:
    """Like _get_manifest_for_file but returns None if file not found."""
    try:
        m, _ = _get_manifest_for_file(store, commit_hash, file_path)
        return m
    except FileNotFoundError:
        return None


def _content_preview(store: ObjectStore, row_hash: str, max_len: int = 60) -> str:
    """Read a row object and return a truncated JSON string."""
    data = store.read("rows", row_hash)
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def blame_file(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
) -> dict:
    target_manifest, _ = _get_manifest_for_file(store, commit_hash, file_path)

    unattributed: set[str] = {e.row_hash for e in target_manifest.entries}
    blame_map: dict[str, BlameEntry] = {}

    current_hash = commit_hash
    current_manifest = target_manifest

    while current_hash and unattributed:
        commit_data = store.read("commits", current_hash)
        if commit_data is None:
            break
        commit = deserialize_commit(commit_data)

        parent_hash = commit.parent_hashes[0] if commit.parent_hashes else None

        if parent_hash is None:
            for i, entry in enumerate(target_manifest.entries):
                if entry.row_hash in unattributed:
                    blame_map[entry.row_hash] = BlameEntry(
                        row_index=i,
                        row_hash=entry.row_hash,
                        commit_hash=current_hash,
                        author=commit.author,
                        timestamp=commit.timestamp,
                        query_fingerprint=entry.query_fingerprint,
                    )
            unattributed.clear()
            break

        parent_manifest = _try_get_manifest(store, parent_hash, file_path)

        if parent_manifest is None:
            for i, entry in enumerate(target_manifest.entries):
                if entry.row_hash in unattributed:
                    blame_map[entry.row_hash] = BlameEntry(
                        row_index=i,
                        row_hash=entry.row_hash,
                        commit_hash=current_hash,
                        author=commit.author,
                        timestamp=commit.timestamp,
                        query_fingerprint=entry.query_fingerprint,
                    )
            unattributed.clear()
            break

        diff = diff_manifests(parent_manifest, current_manifest)

        for added_entry in diff.added:
            if added_entry.row_hash in unattributed:
                idx = next(
                    i for i, e in enumerate(target_manifest.entries)
                    if e.row_hash == added_entry.row_hash
                )
                blame_map[added_entry.row_hash] = BlameEntry(
                    row_index=idx,
                    row_hash=added_entry.row_hash,
                    commit_hash=current_hash,
                    author=commit.author,
                    timestamp=commit.timestamp,
                    query_fingerprint=added_entry.query_fingerprint,
                )
                unattributed.discard(added_entry.row_hash)

        for old_hash, new_hash, qfp in diff.refreshed:
            if new_hash in unattributed:
                idx = next(
                    i for i, e in enumerate(target_manifest.entries)
                    if e.row_hash == new_hash
                )
                blame_map[new_hash] = BlameEntry(
                    row_index=idx,
                    row_hash=new_hash,
                    commit_hash=current_hash,
                    author=commit.author,
                    timestamp=commit.timestamp,
                    query_fingerprint=qfp,
                )
                unattributed.discard(new_hash)

        current_hash = parent_hash
        current_manifest = parent_manifest

    entries = []
    for i, me in enumerate(target_manifest.entries):
        be = blame_map.get(me.row_hash)
        entries.append({
            "row_index": i,
            "row_hash": me.row_hash,
            "commit_hash": be.commit_hash if be else commit_hash,
            "author": be.author if be else "unknown",
            "timestamp": be.timestamp if be else 0,
            "query_fingerprint": me.query_fingerprint,
            "content_preview": _content_preview(store, me.row_hash),
        })

    unique_commits = set(e["commit_hash"] for e in entries)
    unique_authors = set(e["author"] for e in entries)

    return {
        "commit_hash": commit_hash,
        "file": file_path.lstrip("/"),
        "entries": entries,
        "summary": {
            "total_rows": len(entries),
            "unique_commits": len(unique_commits),
            "unique_authors": len(unique_authors),
        },
    }


def row_history(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
    row_index: int,
) -> dict:
    target_manifest, _ = _get_manifest_for_file(store, commit_hash, file_path)

    if row_index < 0 or row_index >= len(target_manifest.entries):
        raise IndexError(
            f"Row index {row_index} out of range (file has {len(target_manifest.entries)} rows)"
        )

    target_entry = target_manifest.entries[row_index]
    qfp = target_entry.query_fingerprint
    tracked_hash = target_entry.row_hash

    events: list[dict] = []
    current_hash = commit_hash
    current_manifest = target_manifest

    while current_hash:
        commit_data = store.read("commits", current_hash)
        if commit_data is None:
            break
        commit = deserialize_commit(commit_data)
        parent_hash = commit.parent_hashes[0] if commit.parent_hashes else None

        if parent_hash is None:
            current_hashes = {e.row_hash for e in current_manifest.entries}
            if tracked_hash in current_hashes:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
            break

        parent_manifest = _try_get_manifest(store, parent_hash, file_path)

        if parent_manifest is None:
            current_hashes = {e.row_hash for e in current_manifest.entries}
            if tracked_hash in current_hashes:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
            break

        diff = diff_manifests(parent_manifest, current_manifest)

        for added_entry in diff.added:
            if added_entry.row_hash == tracked_hash:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "added",
                    "row_hash": tracked_hash,
                    "content_preview": _content_preview(store, tracked_hash),
                })
                tracked_hash = None
                break

        if tracked_hash is None:
            break

        for old_hash, new_hash, r_qfp in diff.refreshed:
            if new_hash == tracked_hash:
                events.append({
                    "commit_hash": current_hash,
                    "author": commit.author,
                    "timestamp": commit.timestamp,
                    "event": "refresh",
                    "row_hash": new_hash,
                    "content_preview": _content_preview(store, new_hash),
                })
                tracked_hash = old_hash
                break

        current_hash = parent_hash
        current_manifest = parent_manifest

    return {
        "commit_hash": commit_hash,
        "file": file_path.lstrip("/"),
        "row_index": row_index,
        "query_fingerprint": qfp,
        "events": events,
    }
