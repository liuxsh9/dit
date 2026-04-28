# src/dit/core/stats.py
"""Repo-level stats aggregated from sidecar objects."""
from __future__ import annotations

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_sidecar
from dit.core.sidecar import sidecar_summary
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def repo_stats(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
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
    Files without a sidecar are included with has_sidecar=False and numeric
    fields set to None. Totals aggregate only files with has_sidecar=True.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_prefix = path_prefix.lstrip("/") if path_prefix else None

    def _manifest_size_bytes(manifest_hash: str) -> int | None:
        manifest_data = store.read("manifests", manifest_hash)
        if manifest_data is None:
            return None
        manifest = deserialize_manifest(manifest_data)
        total = 0
        for entry in manifest.entries:
            row_data = store.read("rows", entry.row_hash)
            if row_data is None:
                return None
            total += len(row_data)
        return total

    files: list[dict] = []
    for path, (obj_type, obj_hash, sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if clean_prefix is not None and not path.startswith(clean_prefix):
            continue

        size_bytes = _manifest_size_bytes(obj_hash)

        if sidecar_hash is None:
            files.append({
                "path": path,
                "row_count": None,
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
            files.append({
                "path": path,
                "row_count": None,
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

    # Compute totals over files with sidecars only
    with_sidecar = [f for f in files if f["has_sidecar"]]
    total_lang: dict[str, int] = {}
    for f in with_sidecar:
        for lang, count in (f["lang_distribution"] or {}).items():
            total_lang[lang] = total_lang.get(lang, 0) + count

    totals: dict = {
        "file_count": len(files),
        "files_with_sidecar": len(with_sidecar),
        "row_count": sum(f["row_count"] for f in with_sidecar) if with_sidecar else None,
        "char_count": sum(f["char_count"] for f in with_sidecar) if with_sidecar else None,
        "size_bytes": sum(f["size_bytes"] for f in files if f["size_bytes"] is not None),
        "token_estimate": sum(f["token_estimate"] for f in with_sidecar) if with_sidecar else None,
        "lang_distribution": total_lang if with_sidecar else {},
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
