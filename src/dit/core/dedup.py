"""Duplicate detection across manifest files in a commit."""
from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def _content_preview(store: ObjectStore, row_hash: str) -> str:
    data = store.read("rows", row_hash)
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > 60:
        return text[:60] + "..."
    return text


def detect_duplicates(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
) -> dict:
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash} not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    row_hash_index: dict[str, list[dict]] = {}
    qfp_index: dict[str, list[dict]] = {}
    total_rows = 0
    total_files = 0

    for path, (obj_type, obj_hash, _sidecar) in flat.items():
        if obj_type != "manifest":
            continue
        if path_prefix and not path.startswith(path_prefix):
            continue

        total_files += 1
        m_data = store.read("manifests", obj_hash)
        if m_data is None:
            continue
        manifest = deserialize_manifest(m_data)

        for idx, entry in enumerate(manifest.entries):
            total_rows += 1
            occ = {"file": path, "row_index": idx}

            row_hash_index.setdefault(entry.row_hash, []).append(occ)

            if entry.query_fingerprint:
                qfp_occ = {
                    "file": path,
                    "row_index": idx,
                    "row_hash": entry.row_hash,
                }
                qfp_index.setdefault(entry.query_fingerprint, []).append(qfp_occ)

    exact_duplicates = []
    for rh, occs in sorted(row_hash_index.items(), key=lambda x: -len(x[1])):
        if len(occs) <= 1:
            continue
        preview = _content_preview(store, rh)
        for o in occs:
            o["content_preview"] = preview
        exact_duplicates.append({
            "row_hash": rh,
            "count": len(occs),
            "occurrences": occs,
        })

    query_duplicates = []
    for qfp, occs in sorted(qfp_index.items(), key=lambda x: -len(x[1])):
        if len(occs) <= 1:
            continue
        distinct_hashes = list(set(o["row_hash"] for o in occs))
        if len(distinct_hashes) <= 1:
            continue
        for o in occs:
            o["content_preview"] = _content_preview(store, o["row_hash"])
        query_duplicates.append({
            "query_fingerprint": qfp,
            "count": len(occs),
            "row_hashes": distinct_hashes,
            "occurrences": occs,
        })

    exact_dup_rows = sum(g["count"] for g in exact_duplicates)
    query_dup_rows = sum(g["count"] for g in query_duplicates)

    if exact_duplicates:
        severity = "warning"
    elif query_duplicates:
        severity = "info"
    else:
        severity = "clean"

    return {
        "commit_hash": commit_hash,
        "exact_duplicates": exact_duplicates,
        "query_duplicates": query_duplicates,
        "summary": {
            "total_rows": total_rows,
            "total_files": total_files,
            "exact_dup_groups": len(exact_duplicates),
            "exact_dup_rows": exact_dup_rows,
            "query_dup_groups": len(query_duplicates),
            "query_dup_rows": query_dup_rows,
            "severity": severity,
        },
    }
