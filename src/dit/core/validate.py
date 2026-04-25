# src/dit/core/validate.py
"""Validation rules loader and commit validator."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dit.core.store import ObjectStore

_DEFAULTS: dict = {
    "required_fields": [],
    "forbidden_keywords": [],
    "max_row_chars": None,
    "min_row_chars": None,
}


def load_rules(repo_root: Path) -> dict:
    """Read .ditvalidate.yaml from repo_root. Returns a ValidationRules dict.

    If the file does not exist, returns default rules (all empty/null).
    Raises ValueError if the YAML is structurally invalid.
    """
    import yaml  # lazy import — yaml is not needed by all callers

    config_path = repo_root / ".ditvalidate.yaml"
    if not config_path.exists():
        return dict(_DEFAULTS)

    raw = yaml.safe_load(config_path.read_text())
    if raw is None:
        return dict(_DEFAULTS)
    if not isinstance(raw, dict):
        raise ValueError("invalid .ditvalidate.yaml: expected mapping at top level")

    required_fields = raw.get("required_fields", [])
    forbidden_keywords = raw.get("forbidden_keywords", [])
    max_row_chars = raw.get("max_row_chars", None)
    min_row_chars = raw.get("min_row_chars", None)

    # Type validation
    if not isinstance(required_fields, list):
        raise ValueError("invalid .ditvalidate.yaml: required_fields must be a list")
    if not isinstance(forbidden_keywords, list):
        raise ValueError("invalid .ditvalidate.yaml: forbidden_keywords must be a list")
    if max_row_chars is not None and (not isinstance(max_row_chars, int) or max_row_chars <= 0):
        raise ValueError("invalid .ditvalidate.yaml: max_row_chars must be a positive integer")
    if min_row_chars is not None and (not isinstance(min_row_chars, int) or min_row_chars <= 0):
        raise ValueError("invalid .ditvalidate.yaml: min_row_chars must be a positive integer")

    return {
        "required_fields": [str(f) for f in required_fields],
        "forbidden_keywords": [str(k) for k in forbidden_keywords],
        "max_row_chars": max_row_chars,
        "min_row_chars": min_row_chars,
    }


def validate_commit(
    store: "ObjectStore",
    commit_hash: str,
    rules: dict,
) -> dict:
    """Validate all JSONL rows in a commit against the given rules.

    Returns:
    {
      "status": "pass" | "fail",
      "violations": [...],
      "checked_rows": int,
    }

    Raises FileNotFoundError if commit_hash is not found in store.
    All violations are collected; the function never short-circuits early.
    """
    from dit.core.objects import deserialize_commit, deserialize_manifest
    from dit.core.tree_walker import flatten_tree

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    required_fields: list[str] = rules.get("required_fields") or []
    forbidden_keywords: list[str] = rules.get("forbidden_keywords") or []
    max_row_chars: int | None = rules.get("max_row_chars")
    min_row_chars: int | None = rules.get("min_row_chars")

    violations: list[dict] = []
    checked_rows = 0

    for path, (obj_type, obj_hash, _sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue

        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            continue

        manifest = deserialize_manifest(manifest_data)

        for row_index, entry in enumerate(manifest.entries):
            row_bytes = store.read("rows", entry.row_hash)
            if row_bytes is None:
                checked_rows += 1
                continue

            row_dict = json.loads(row_bytes)
            # Compact JSON string used for keyword and char-count checks
            row_json = json.dumps(row_dict, ensure_ascii=False, separators=(",", ":"))
            row_json_lower = row_json.lower()
            checked_rows += 1

            base = {"file": path, "row_index": row_index, "row_hash": entry.row_hash}

            # 1. required_fields
            for field_name in required_fields:
                if field_name not in row_dict:
                    violations.append({
                        **base,
                        "rule": "required_fields",
                        "detail": f"missing field: {field_name}",
                    })

            # 2. forbidden_keywords
            for keyword in forbidden_keywords:
                if keyword.lower() in row_json_lower:
                    violations.append({
                        **base,
                        "rule": "forbidden_keywords",
                        "detail": f'keyword "{keyword}" found',
                    })

            # 3. max_row_chars
            if max_row_chars is not None and len(row_json) > max_row_chars:
                violations.append({
                    **base,
                    "rule": "max_row_chars",
                    "detail": f"row has {len(row_json)} chars (limit {max_row_chars})",
                })

            # 4. min_row_chars
            if min_row_chars is not None and len(row_json) < min_row_chars:
                violations.append({
                    **base,
                    "rule": "min_row_chars",
                    "detail": f"row has {len(row_json)} chars (minimum {min_row_chars})",
                })

    status = "fail" if violations else "pass"
    return {"status": status, "violations": violations, "checked_rows": checked_rows}
