# src/dit/core/search.py
"""Brute-force row-level search across JSONL rows in a commit."""
from __future__ import annotations

import json
import re

from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def _resolve_field(row: dict, field_path: str) -> object | None:
    """Navigate nested dict/list using dot-notation with bracket indexing.

    Examples:
      "instruction"             -> row["instruction"]
      "messages[0].content"    -> row["messages"][0]["content"]
      "meta.source"            -> row["meta"]["source"]

    Returns None if the path is missing or types don't match.
    """
    # Split on "." but keep bracket notation attached to the segment before the dot
    segments = field_path.split(".")
    current = row
    for segment in segments:
        if current is None:
            return None
        # Check for list index: key[N]
        m = re.fullmatch(r"([^\[]+)\[(\d+)\]", segment)
        if m:
            key, idx = m.group(1), int(m.group(2))
            if not isinstance(current, dict) or key not in current:
                return None
            current = current[key]
            if not isinstance(current, list) or idx >= len(current):
                return None
            current = current[idx]
        else:
            # Plain key
            if not isinstance(current, dict) or segment not in current:
                return None
            current = current[segment]
    return current


def _make_highlight(text: str, query: str, context: int = 20) -> str:
    """Return a short excerpt with the matched substring in context.

    Returns at most `context` characters before and after the match position,
    with '...' prepended/appended if the surrounding text was trimmed.
    """
    pos = text.lower().find(query.lower())
    if pos == -1:
        return text[:context * 2 + len(query)]

    start = max(0, pos - context)
    end = min(len(text), pos + len(query) + context)

    excerpt = text[start:end]

    if start > 0:
        excerpt = "..." + excerpt
    if end < len(text):
        excerpt = excerpt + "..."

    return excerpt


def search_rows(
    store: ObjectStore,
    commit_hash: str,
    query: str,
    *,
    path_prefix: str | None = None,
    field_path: str | None = None,
    limit: int = 50,
) -> dict:
    """Brute-force substring search across JSONL rows in a commit.

    Returns:
    {
      "commit_hash": "abc12345...",
      "query": "LRU缓存",
      "field_path": "messages[0].content",   # or None
      "matches": [
        {
          "file": "train.jsonl",
          "row_index": 42,
          "row_hash": "abc...",
          "content": { <full row as dict> },
          "highlight": "...实现一个LRU缓存，支持get和put..."
        },
        ...
      ],
      "total_scanned": 1700,
      "limit_reached": False
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    Matching is case-insensitive substring search.
    Scanning stops once `limit` matches are collected.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_prefix = path_prefix.lstrip("/") if path_prefix else None
    query_lower = query.lower()

    matches: list[dict] = []
    total_scanned = 0
    limit_reached = False

    for path, (obj_type, obj_hash, _sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if clean_prefix is not None and not path.startswith(clean_prefix):
            continue

        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            continue

        manifest = deserialize_manifest(manifest_data)

        for row_index, entry in enumerate(manifest.entries):
            row_bytes = store.read("rows", entry.row_hash)
            if row_bytes is None:
                total_scanned += 1
                continue

            row = json.loads(row_bytes)
            total_scanned += 1

            # Match check
            if field_path is None:
                # Full-row mode: serialize to JSON string and search
                text = json.dumps(row, ensure_ascii=False)
                matched = query_lower in text.lower()
                excerpt_source = text
            else:
                # Field mode: extract the field value
                value = _resolve_field(row, field_path)
                if value is None:
                    continue
                value_str = str(value) if not isinstance(value, str) else value
                matched = query_lower in value_str.lower()
                excerpt_source = value_str

            if matched:
                matches.append({
                    "file": path,
                    "row_index": row_index,
                    "row_hash": entry.row_hash,
                    "content": row,
                    "highlight": _make_highlight(excerpt_source, query),
                })

                if len(matches) == limit:
                    limit_reached = True
                    return {
                        "commit_hash": commit_hash,
                        "query": query,
                        "field_path": field_path,
                        "matches": matches,
                        "total_scanned": total_scanned,
                        "limit_reached": limit_reached,
                    }

    return {
        "commit_hash": commit_hash,
        "query": query,
        "field_path": field_path,
        "matches": matches,
        "total_scanned": total_scanned,
        "limit_reached": limit_reached,
    }
