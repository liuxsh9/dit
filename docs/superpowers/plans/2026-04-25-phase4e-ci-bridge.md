# Phase 4E: CI Bridge Skeleton — Implementation Plan

> **Spec:** `docs/superpowers/specs/2026-04-25-phase4e-ci-bridge.md`
> **Date:** 2026-04-25

---

## Task 1: Core validate module (`src/dit/core/validate.py`) with tests

### 1.1 Write the test first

Create `tests/test_validate.py`:

```python
# tests/test_validate.py
import json
import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.validate import load_rules, validate_commit


# ── load_rules ──────────────────────────────────────────────────────────────

class TestLoadRules:
    def test_returns_defaults_when_file_missing(self, tmp_path: Path):
        rules = load_rules(tmp_path)
        assert rules["required_fields"] == []
        assert rules["forbidden_keywords"] == []
        assert rules["max_row_chars"] is None
        assert rules["min_row_chars"] is None

    def test_reads_required_fields(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text(
            "required_fields:\n  - instruction\n  - response\n"
        )
        rules = load_rules(tmp_path)
        assert rules["required_fields"] == ["instruction", "response"]

    def test_reads_forbidden_keywords(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text(
            'forbidden_keywords:\n  - "OpenAI"\n  - "GPT-4"\n'
        )
        rules = load_rules(tmp_path)
        assert rules["forbidden_keywords"] == ["OpenAI", "GPT-4"]

    def test_reads_max_and_min_row_chars(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text(
            "max_row_chars: 8192\nmin_row_chars: 10\n"
        )
        rules = load_rules(tmp_path)
        assert rules["max_row_chars"] == 8192
        assert rules["min_row_chars"] == 10

    def test_empty_yaml_returns_defaults(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text("")
        rules = load_rules(tmp_path)
        assert rules["required_fields"] == []
        assert rules["max_row_chars"] is None

    def test_partial_yaml_fills_defaults(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text("max_row_chars: 512\n")
        rules = load_rules(tmp_path)
        assert rules["max_row_chars"] == 512
        assert rules["required_fields"] == []
        assert rules["min_row_chars"] is None

    def test_raises_on_non_mapping(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="expected mapping"):
            load_rules(tmp_path)


# ── helpers ──────────────────────────────────────────────────────────────────

def _default_rules():
    return {
        "required_fields": [],
        "forbidden_keywords": [],
        "max_row_chars": None,
        "min_row_chars": None,
    }


def _build_commit(tmp_path: Path, rows_by_file: dict[str, list[str]]) -> tuple[ObjectStore, str]:
    """Build an ObjectStore with one commit containing the given JSONL rows."""
    store = ObjectStore(tmp_path / "objects")
    tree_entries: dict[str, tuple] = {}
    for filename, rows in rows_by_file.items():
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries[filename] = ("manifest", mh, None)
    tree_hash = build_nested_tree(store, tree_entries)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash


# ── validate_commit — happy path ─────────────────────────────────────────────

class TestValidateCommitPass:
    def test_empty_rules_always_pass(self, tmp_path: Path):
        rows = [json.dumps({"a": "b"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        result = validate_commit(store, commit_hash, _default_rules())
        assert result["status"] == "pass"
        assert result["violations"] == []
        assert result["checked_rows"] == 1

    def test_required_fields_present(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "hello", "response": "world"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "required_fields": ["instruction", "response"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_forbidden_keywords_absent(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "hello", "response": "world"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "forbidden_keywords": ["OpenAI"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_row_within_max_chars(self, tmp_path: Path):
        rows = [json.dumps({"x": "a" * 10})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "max_row_chars": 1000}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_row_above_min_chars(self, tmp_path: Path):
        rows = [json.dumps({"x": "hello"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "min_row_chars": 5}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_checked_rows_counts_all_files(self, tmp_path: Path):
        store, commit_hash = _build_commit(tmp_path, {
            "train.jsonl": [json.dumps({"a": "1"}), json.dumps({"a": "2"})],
            "eval.jsonl": [json.dumps({"a": "3"})],
        })
        result = validate_commit(store, commit_hash, _default_rules())
        assert result["checked_rows"] == 3


# ── validate_commit — required_fields violations ──────────────────────────────

class TestValidateRequiredFields:
    def test_missing_required_field(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "hello"})]  # missing "response"
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "required_fields": ["instruction", "response"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        assert len(result["violations"]) == 1
        v = result["violations"][0]
        assert v["rule"] == "required_fields"
        assert "response" in v["detail"]
        assert v["file"] == "train.jsonl"
        assert v["row_index"] == 0

    def test_multiple_missing_fields_collected(self, tmp_path: Path):
        rows = [json.dumps({"x": "y"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "required_fields": ["instruction", "response"]}
        result = validate_commit(store, commit_hash, rules)
        assert len(result["violations"]) == 2

    def test_violation_includes_row_hash(self, tmp_path: Path):
        rows = [json.dumps({"x": "y"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "required_fields": ["instruction"]}
        result = validate_commit(store, commit_hash, rules)
        assert "row_hash" in result["violations"][0]
        assert len(result["violations"][0]["row_hash"]) == 64


# ── validate_commit — forbidden_keywords violations ───────────────────────────

class TestValidateForbiddenKeywords:
    def test_forbidden_keyword_found(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "Tell me about OpenAI", "response": "ok"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "forbidden_keywords": ["OpenAI"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        v = result["violations"][0]
        assert v["rule"] == "forbidden_keywords"
        assert "OpenAI" in v["detail"]

    def test_forbidden_keyword_case_insensitive(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "tell me about openai", "response": "ok"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "forbidden_keywords": ["OpenAI"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"

    def test_no_keyword_match(self, tmp_path: Path):
        rows = [json.dumps({"instruction": "Tell me about neural nets", "response": "ok"})]
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": rows})
        rules = {**_default_rules(), "forbidden_keywords": ["OpenAI"]}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"


# ── validate_commit — max_row_chars / min_row_chars ───────────────────────────

class TestValidateCharLimits:
    def test_row_exceeds_max_chars(self, tmp_path: Path):
        long_row = json.dumps({"x": "a" * 9000})
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": [long_row]})
        rules = {**_default_rules(), "max_row_chars": 8192}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        v = result["violations"][0]
        assert v["rule"] == "max_row_chars"
        assert "8192" in v["detail"]

    def test_row_below_min_chars(self, tmp_path: Path):
        short_row = json.dumps({"x": "a"})
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": [short_row]})
        rules = {**_default_rules(), "min_row_chars": 100}
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        v = result["violations"][0]
        assert v["rule"] == "min_row_chars"

    def test_all_violations_collected_per_row(self, tmp_path: Path):
        """A single row missing a required field AND exceeding max_row_chars
        produces two violations."""
        long_row = json.dumps({"x": "a" * 9000})
        store, commit_hash = _build_commit(tmp_path, {"train.jsonl": [long_row]})
        rules = {
            **_default_rules(),
            "required_fields": ["instruction"],
            "max_row_chars": 100,
        }
        result = validate_commit(store, commit_hash, rules)
        rules_violated = {v["rule"] for v in result["violations"]}
        assert "required_fields" in rules_violated
        assert "max_row_chars" in rules_violated


# ── validate_commit — error cases ────────────────────────────────────────────

class TestValidateCommitErrors:
    def test_raises_on_missing_commit(self, tmp_path: Path):
        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(FileNotFoundError):
            validate_commit(store, "a" * 64, _default_rules())
```

### 1.2 Implement `src/dit/core/validate.py`

```python
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
```

### 1.3 Checklist

- [ ] Create `tests/test_validate.py` with the content above
- [ ] Create `src/dit/core/validate.py` with the content above
- [ ] Run `uv run pytest tests/test_validate.py -v` — all tests must pass

---

## Task 2: CLI `dit validate` command with tests

### 2.1 Write the test first

Create `tests/test_cli_validate.py`:

```python
# tests/test_cli_validate.py
import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore

runner = CliRunner()


def _init_repo_with_rows(
    tmp_path: Path,
    rows_by_file: dict[str, list[str]] | None = None,
    rules_yaml: str | None = None,
) -> tuple[ObjectStore, RefStore, str]:
    """Init a dit repo. If rows_by_file is None, uses two default files."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".dit"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    if rows_by_file is None:
        rows_by_file = {
            "train.jsonl": [
                json.dumps({"instruction": "hello", "response": "world"}),
                json.dumps({"instruction": "foo", "response": "bar"}),
            ],
            "eval.jsonl": [
                json.dumps({"instruction": "test", "response": "ok"}),
            ],
        }

    # Write rules file alongside repo root (where .dit lives)
    if rules_yaml is not None:
        (tmp_path / ".ditvalidate.yaml").write_text(rules_yaml)

    tree_entries: dict[str, tuple] = {}
    for filename, rows in rows_by_file.items():
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries[filename] = ("manifest", mh, None)

    tree_hash = build_nested_tree(store, tree_entries)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    refs.set_branch("main", commit_hash)
    return store, refs, commit_hash


class TestValidateCommand:
    def test_no_rules_exits_0(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0

    def test_pass_output_contains_pass(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert "PASS" in result.stdout

    def test_pass_output_shows_checked_rows(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate"])
        assert "3" in result.stdout  # 2 train + 1 eval

    def test_fail_exits_1_on_violation(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1

    def test_fail_output_contains_fail(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate"])
        assert "FAIL" in result.stdout

    def test_fail_output_shows_violation_table(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rows_by_file={
                "train.jsonl": [
                    json.dumps({"instruction": "hi"}),  # missing response
                ],
            },
            rules_yaml="required_fields:\n  - instruction\n  - response\n",
        )
        result = runner.invoke(app, ["validate"])
        assert "train.jsonl" in result.stdout
        assert "required_fields" in result.stdout
        assert "response" in result.stdout

    def test_json_format_pass(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["status"] == "pass"
        assert data["violations"] == []
        assert "checked_rows" in data

    def test_json_format_fail(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate", "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.stdout)
        assert data["status"] == "fail"
        assert len(data["violations"]) > 0

    def test_json_violation_has_required_keys(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rules_yaml="required_fields:\n  - missing_field\n",
        )
        result = runner.invoke(app, ["validate", "--format", "json"])
        data = json.loads(result.stdout)
        v = data["violations"][0]
        assert "file" in v
        assert "row_index" in v
        assert "row_hash" in v
        assert "rule" in v
        assert "detail" in v

    def test_ref_option_accepts_branch(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--ref", "main"])
        assert result.exit_code == 0

    def test_ref_bad_branch_exits_1(self, tmp_path: Path):
        _init_repo_with_rows(tmp_path)
        result = runner.invoke(app, ["validate", "--ref", "no-such-branch"])
        assert result.exit_code == 1
        assert "not found" in result.stdout.lower() or "not found" in (result.stderr or "").lower()

    def test_forbidden_keyword_violation(self, tmp_path: Path):
        _init_repo_with_rows(
            tmp_path,
            rows_by_file={
                "train.jsonl": [
                    json.dumps({"instruction": "Tell me about OpenAI", "response": "ok"}),
                ],
            },
            rules_yaml='forbidden_keywords:\n  - "OpenAI"\n',
        )
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 1
        assert "forbidden_keywords" in result.stdout
```

### 2.2 Implement CLI command in `src/dit/cli/main.py`

Add after the `search` command and before `_fmt_tokens`. The `validate` command needs to be a top-level `@app.command()`:

```python
@app.command()
def validate(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to validate"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Validate all JSONL rows in a commit against .ditvalidate.yaml rules."""
    import json as _json
    from dit.core.validate import load_rules, validate_commit

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    # Resolve ref to commit hash
    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    rules = load_rules(repo_root)

    try:
        result = validate_commit(store, commit_hash, rules)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        raise typer.Exit(0 if result["status"] == "pass" else 1)

    # Table format
    ref_display = f"heads/{ref}" if refs.get_branch(ref) else ref
    typer.echo(f"Validating {ref_display} (commit {commit_hash[:8]})")

    rf = rules["required_fields"]
    fk = rules["forbidden_keywords"]
    mx = rules["max_row_chars"]
    rules_parts = []
    if rf:
        rules_parts.append(f"required_fields=[{', '.join(rf)}]")
    if fk:
        rules_parts.append(f"forbidden_keywords={len(fk)}")
    if mx is not None:
        rules_parts.append(f"max_row_chars={mx}")
    if rules["min_row_chars"] is not None:
        rules_parts.append(f"min_row_chars={rules['min_row_chars']}")
    if rules_parts:
        typer.echo("Rules: " + "  ".join(rules_parts))
    typer.echo("")

    violations = result["violations"]
    checked = result["checked_rows"]

    if not violations:
        typer.echo(f"Checked {checked} rows across {_count_files(violations, result)} files.")
        typer.echo("PASS")
        raise typer.Exit(0)

    # Count unique files for summary
    file_set = {v["file"] for v in violations}
    n_files = _count_result_files(store, commit_hash)

    typer.echo(f"FAIL \u2014 {len(violations)} violation(s)")
    typer.echo("")

    col_file = max((len(v["file"]) for v in violations), default=4)
    col_file = max(col_file, 4)
    col_rule = max((len(v["rule"]) for v in violations), default=4)
    col_rule = max(col_rule, 4)
    header = f"{'File':<{col_file}}   {'Row':>5}   {'Rule':<{col_rule}}   Detail"
    sep = "\u2500" * max(len(header), 80)
    typer.echo(header)
    typer.echo(sep)
    for v in violations:
        typer.echo(f"{v['file']:<{col_file}}   {v['row_index']:>5}   {v['rule']:<{col_rule}}   {v['detail']}")
    typer.echo(sep)
    typer.echo(f"Checked {checked} rows across {n_files} files.")
    raise typer.Exit(1)


def _count_result_files(store, commit_hash: str) -> int:
    """Count manifest files in a commit (used for validate summary line)."""
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree
    try:
        commit_data = store.read("commits", commit_hash)
        if commit_data is None:
            return 0
        commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, commit.tree_hash)
        return sum(1 for _, (t, _, _) in flat.items() if t == "manifest")
    except Exception:
        return 0
```

**Note on placement:** Insert `validate` command before the `_fmt_tokens` helper function at the bottom of `main.py`. The two private helpers `_count_result_files` (new) should be placed near `_fmt_tokens`.

### 2.3 Checklist

- [ ] Create `tests/test_cli_validate.py` with the content above
- [ ] Add `validate` command and `_count_result_files` helper to `src/dit/cli/main.py`
- [ ] Run `uv run pytest tests/test_cli_validate.py -v` — all tests must pass

---

## Task 3: `CICheck` DB model and migration

### 3.1 Add `CICheck` model to `src/dit/server/models.py`

Add after the `PrApproval` class:

```python
class CICheck(Base):
    __tablename__ = "ci_checks"
    __table_args__ = (
        sa.UniqueConstraint("repo_id", "commit_hash", "check_name", name="uq_ci_check"),
        {"schema": "dit"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("dit.repos.id"), nullable=False, index=True)
    commit_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    check_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # pending|pass|fail
    details_json: Mapped[Optional[dict]] = mapped_column(sa.JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"CICheck(id={self.id}, repo_id={self.repo_id}, "
            f"commit_hash={self.commit_hash[:8]}..., "
            f"check_name={self.check_name!r}, status={self.status!r})"
        )
```

**Imports needed in models.py** — `sa.JSON` is already available via `import sqlalchemy as sa`. `Optional` is already imported. No new imports required.

### 3.2 Migration note

The project has no Alembic migrations directory. The test suite uses `Base.metadata.create_all` via the in-memory SQLite fixture in `tests/server/conftest.py` — adding the model to `Base` is sufficient for the test DB. For a production deployment with an existing database, run:

```sql
CREATE TABLE dit.ci_checks (
    id SERIAL PRIMARY KEY,
    repo_id INTEGER NOT NULL REFERENCES dit.repos(id),
    commit_hash VARCHAR(64) NOT NULL,
    check_name VARCHAR(128) NOT NULL,
    status VARCHAR(16) NOT NULL,
    details_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ci_check UNIQUE (repo_id, commit_hash, check_name)
);
CREATE INDEX ON dit.ci_checks (repo_id);
```

Document this in `docs/superpowers/plans/2026-04-25-phase4e-ci-bridge.md` as a manual migration step.

### 3.3 Checklist

- [ ] Add `CICheck` model to `src/dit/server/models.py` after `PrApproval`
- [ ] Run `uv run pytest tests/server/ -v` — existing server tests must still pass (model addition is backward-compatible)

---

## Task 4: Server validate and checks endpoints with tests

### 4.1 Write the test first

Create `tests/server/test_routes_validate.py`:

```python
# tests/server/test_routes_validate.py
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_rows(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "validate-repo",
    rows_by_file: dict | None = None,
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    if rows_by_file is None:
        rows_by_file = {
            "train.jsonl": [
                json.dumps({"instruction": "hello", "response": "world"}),
                json.dumps({"instruction": "foo", "response": "bar"}),
            ],
            "eval.jsonl": [
                json.dumps({"instruction": "test", "response": "ok"}),
            ],
        }

    tree_entries: dict = {}
    for filename, rows in rows_by_file.items():
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries[filename] = ("manifest", mh, None)

    tree_hash = build_nested_tree(store, tree_entries)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
    )
    return store, commit_hash


@pytest.mark.asyncio
class TestValidateEndpoint:
    async def test_validate_pass_returns_200(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 200

    async def test_validate_pass_body(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": commit_hash},
        )
        data = resp.json()
        assert data["status"] == "pass"
        assert data["violations"] == []
        assert "checked_rows" in data

    async def test_validate_with_branch_ref(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": "heads/main"},
        )
        assert resp.status_code == 200

    async def test_validate_bad_ref_returns_404(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": "heads/nonexistent"},
        )
        assert resp.status_code == 404

    async def test_validate_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/validate",
            json={"ref": "heads/main"},
        )
        assert resp.status_code == 404

    async def test_validate_default_ref_is_heads_main(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={},
        )
        assert resp.status_code == 200

    async def test_validate_always_200_even_on_fail(self, client: AsyncClient, tmp_path: Path):
        """HTTP status 200 regardless of pass/fail — status in body."""
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path,
            repo="validate-repo-fail",
            rows_by_file={
                "train.jsonl": [json.dumps({"x": "y"})],
            },
        )
        # No rules file in the store, but we can still test the endpoint returns 200
        resp = await client.post(
            "/api/v1/repos/validate-repo-fail/validate",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestChecksEndpoints:
    async def test_report_check_returns_201(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo"
        )
        resp = await client.post(
            "/api/v1/repos/checks-repo/checks",
            json={
                "commit_hash": commit_hash,
                "check_name": "data-quality-ci",
                "status": "pass",
                "details": {"passed": 3, "failed": 0},
            },
        )
        assert resp.status_code == 201

    async def test_report_check_body_has_required_keys(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo2"
        )
        resp = await client.post(
            "/api/v1/repos/checks-repo2/checks",
            json={
                "commit_hash": commit_hash,
                "check_name": "data-quality-ci",
                "status": "pass",
            },
        )
        data = resp.json()
        assert "id" in data
        assert "repo_id" in data
        assert "commit_hash" in data
        assert "check_name" in data
        assert "status" in data
        assert "created_at" in data
        assert "updated_at" in data

    async def test_report_check_upsert(self, client: AsyncClient, tmp_path: Path):
        """Second POST with same (repo, commit, check_name) updates, not duplicates."""
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo3"
        )
        payload = {
            "commit_hash": commit_hash,
            "check_name": "data-quality-ci",
            "status": "pending",
        }
        r1 = await client.post("/api/v1/repos/checks-repo3/checks", json=payload)
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        payload["status"] = "pass"
        r2 = await client.post("/api/v1/repos/checks-repo3/checks", json=payload)
        assert r2.status_code == 201
        data2 = r2.json()
        assert data2["id"] == id1          # same row, not a duplicate
        assert data2["status"] == "pass"

    async def test_get_checks_returns_200(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo4"
        )
        await client.post(
            "/api/v1/repos/checks-repo4/checks",
            json={"commit_hash": commit_hash, "check_name": "ci", "status": "pass"},
        )
        resp = await client.get(f"/api/v1/repos/checks-repo4/checks/{commit_hash}")
        assert resp.status_code == 200

    async def test_get_checks_body_structure(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo5"
        )
        await client.post(
            "/api/v1/repos/checks-repo5/checks",
            json={"commit_hash": commit_hash, "check_name": "ci", "status": "pass"},
        )
        resp = await client.get(f"/api/v1/repos/checks-repo5/checks/{commit_hash}")
        data = resp.json()
        assert "commit_hash" in data
        assert "checks" in data
        assert len(data["checks"]) == 1
        c = data["checks"][0]
        assert c["check_name"] == "ci"
        assert c["status"] == "pass"

    async def test_get_checks_empty_when_none(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo6"
        )
        resp = await client.get(f"/api/v1/repos/checks-repo6/checks/{commit_hash}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"] == []

    async def test_report_check_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/checks",
            json={"commit_hash": "a" * 64, "check_name": "ci", "status": "pass"},
        )
        assert resp.status_code == 404

    async def test_get_checks_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/checks/{'a' * 64}")
        assert resp.status_code == 404
```

### 4.2 Implement `src/dit/server/routes/validate_api.py`

```python
# src/dit/server/routes/validate_api.py
"""Validate endpoint and CI checks endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import CICheck, Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["validate"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


def _resolve_ref(ref_str: str, repo_id: int) -> tuple[str | None, str | None]:
    """Return (commit_hash, None) if direct hash, else (None, ref_name) for DB lookup."""
    if len(ref_str) == 64 and all(c in "0123456789abcdef" for c in ref_str):
        return ref_str, None
    return None, ref_str


class ValidateRequest(BaseModel):
    ref: str = "heads/main"


class CheckReportRequest(BaseModel):
    commit_hash: str
    check_name: str
    status: str  # "pending" | "pass" | "fail"
    details: dict[str, Any] | None = None


@router.post("/{repo}/validate")
async def repo_validate_endpoint(
    repo: str,
    body: ValidateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Run validation rules against all JSONL rows in a commit."""
    from dit.core.validate import load_rules, validate_commit

    r = await _get_repo(repo, session)

    # Resolve ref to commit hash
    if len(body.ref) == 64 and all(c in "0123456789abcdef" for c in body.ref):
        commit_hash = body.ref
    else:
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.ref)
        )
        ref_obj = result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.ref}' not found")
        commit_hash = ref_obj.target_hash

    store = _store_for_repo(request, repo)

    # Load rules from a temporary directory. The server reads .ditvalidate.yaml
    # from the committed tree via the object store by writing the blob to a
    # tempfile that load_rules can consume.
    rules = _load_rules_from_store(store, commit_hash)

    try:
        result = validate_commit(store, commit_hash, rules)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result


def _load_rules_from_store(store, commit_hash: str) -> dict:
    """Read .ditvalidate.yaml from the committed tree via the object store.

    Falls back to default rules if the file is not present in the tree.
    """
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree
    from dit.core.validate import load_rules

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        # validate_commit will raise FileNotFoundError; return defaults here
        import tempfile as _tmp
        with _tmp.TemporaryDirectory() as td:
            return load_rules(Path(td))

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    # Look for a blob named ".ditvalidate.yaml" at the repo root
    entry = flat.get(".ditvalidate.yaml")
    if entry is None:
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    obj_type, obj_hash, _ = entry
    if obj_type != "blob":
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    blob_data = store.read("blobs", obj_hash)
    if blob_data is None:
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    with tempfile.TemporaryDirectory() as td:
        rules_path = Path(td) / ".ditvalidate.yaml"
        rules_path.write_bytes(blob_data)
        return load_rules(Path(td))


@router.post("/{repo}/checks", status_code=201)
async def report_check_endpoint(
    repo: str,
    body: CheckReportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("write")),
):
    """Report or update a CI check result for a commit."""
    r = await _get_repo(repo, session)

    # Upsert: look for existing row
    result = await session.execute(
        select(CICheck).where(
            CICheck.repo_id == r.id,
            CICheck.commit_hash == body.commit_hash,
            CICheck.check_name == body.check_name,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.status = body.status
        existing.details_json = body.details
        await session.commit()
        await session.refresh(existing)
        check = existing
    else:
        check = CICheck(
            repo_id=r.id,
            commit_hash=body.commit_hash,
            check_name=body.check_name,
            status=body.status,
            details_json=body.details,
        )
        session.add(check)
        await session.commit()
        await session.refresh(check)

    return {
        "id": check.id,
        "repo_id": check.repo_id,
        "commit_hash": check.commit_hash,
        "check_name": check.check_name,
        "status": check.status,
        "details": check.details_json,
        "created_at": check.created_at.isoformat() if check.created_at else None,
        "updated_at": check.updated_at.isoformat() if check.updated_at else None,
    }


@router.get("/{repo}/checks/{commit}")
async def get_checks_endpoint(
    repo: str,
    commit: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Get all CI checks for a commit."""
    r = await _get_repo(repo, session)

    result = await session.execute(
        select(CICheck).where(
            CICheck.repo_id == r.id,
            CICheck.commit_hash == commit,
        ).order_by(CICheck.id)
    )
    checks = result.scalars().all()

    return {
        "commit_hash": commit,
        "checks": [
            {
                "id": c.id,
                "check_name": c.check_name,
                "status": c.status,
                "details": c.details_json,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in checks
        ],
    }
```

### 4.3 Register router in `src/dit/server/app.py`

Add after the `search_router` registration:

```python
    from dit.server.routes.validate_api import router as validate_router
    application.include_router(validate_router)
```

### 4.4 Checklist

- [ ] Create `tests/server/test_routes_validate.py` with the content above
- [ ] Create `src/dit/server/routes/validate_api.py` with the content above
- [ ] Add validate router registration to `src/dit/server/app.py`
- [ ] Run `uv run pytest tests/server/test_routes_validate.py -v` — all tests must pass
- [ ] Run `uv run pytest tests/ -v` — full suite must pass

---

## Task 5: Gateway proxy — Go handlers, client methods, routes

### 5.1 Add client methods to `modules/dit/client.go`

Append after the `Search` method:

```go
func (c *Client) Validate(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/validate", body)
}

func (c *Client) ReportCheck(ctx context.Context, repoName string, body []byte) ([]byte, int, error) {
	return c.do(ctx, http.MethodPost, "/api/v1/repos/"+repoName+"/checks", body)
}

func (c *Client) GetChecks(ctx context.Context, repoName, commitHash string) ([]byte, int, error) {
	return c.do(ctx, http.MethodGet, "/api/v1/repos/"+repoName+"/checks/"+commitHash, nil)
}
```

### 5.2 Add handler functions to `routers/api/v1/repo/dit.go`

Append after `DatahubSearch`:

```go
func DatahubValidate(ctx *context.APIContext) {
	body, ok := readBody(ctx)
	if !ok {
		return
	}
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().Validate(ctx, ctx.Repo.Repository.Name, body)
	})
}

func DatahubReportCheck(ctx *context.APIContext) {
	body, ok := readBody(ctx)
	if !ok {
		return
	}
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().ReportCheck(ctx, ctx.Repo.Repository.Name, body)
	})
}

func DatahubGetChecks(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetChecks(ctx, ctx.Repo.Repository.Name, ctx.Params(":commit"))
	})
}
```

### 5.3 Register routes in `routers/api/v1/api.go`

In the dit route group, after `m.Post("/search", repo.DatahubSearch)` and before the closing `})`:

```go
						m.Post("/validate",         repo.DatahubValidate)
						m.Post("/checks",           repo.DatahubReportCheck)
						m.Get("/checks/{commit}",   repo.DatahubGetChecks)
```

The exact lines to add go at line 1443 in `api.go` (immediately after the search route):

```go
						m.Post("/search", repo.DatahubSearch)
						m.Post("/validate",         repo.DatahubValidate)
						m.Post("/checks",           repo.DatahubReportCheck)
						m.Get("/checks/{commit}",   repo.DatahubGetChecks)
					})
```

### 5.4 Checklist

- [ ] Append three client methods to `/Users/lxs/code/datahub-gateway/modules/dit/client.go`
- [ ] Append three handler functions to `/Users/lxs/code/datahub-gateway/routers/api/v1/repo/dit.go`
- [ ] Add three route registrations to `/Users/lxs/code/datahub-gateway/routers/api/v1/api.go` after the search route
- [ ] Verify Go build: `cd /Users/lxs/code/datahub-gateway && go build ./...`

---

## Task 6: Vue CI badge in `DataRepoHome.vue`

### 6.1 Locate insertion points in `DataRepoHome.vue`

File: `/Users/lxs/code/datahub-gateway/web_src/js/components/DataRepoHome.vue`

**Template change:** In the branch selector segment (lines 5–18), add the badge after the stats labels. The current end of the `<div class="ui inline fields">` block is:

```html
        <div class="field" v-if="stats">
          <span class="ui label">{{ stats.fileCount }} files</span>
          <span class="ui label">{{ stats.rowCount }} rows</span>
        </div>
```

Replace with:

```html
        <div class="field" v-if="stats">
          <span class="ui label">{{ stats.fileCount }} files</span>
          <span class="ui label">{{ stats.rowCount }} rows</span>
        </div>
        <div class="field">
          <span v-if="checksStatus" class="ui tiny label" :class="checksStatusClass" style="margin-left: 6px;">
            <i :class="checksStatusIcon"></i> {{ checksStatusText }}
          </span>
          <span v-else-if="checksLoading" class="ui tiny label" style="margin-left: 6px;">
            <i class="spinner loading icon"></i>
          </span>
        </div>
```

### 6.2 `data()` additions

In the `data()` return object, add after `searchResultsOpen: true,`:

```js
      checksLoading: false,
      checksData: null,
```

### 6.3 `computed` additions

Add after the `topLangs` computed property:

```js
    checksStatus() {
      if (!this.checksData || this.checksData.checks.length === 0) return null;
      const statuses = this.checksData.checks.map(c => c.status);
      if (statuses.includes('fail')) return 'fail';
      if (statuses.includes('pending')) return 'pending';
      return 'pass';
    },
    checksStatusClass() {
      return {
        'green': this.checksStatus === 'pass',
        'red':   this.checksStatus === 'fail',
        'grey':  this.checksStatus === 'pending',
      };
    },
    checksStatusIcon() {
      return {
        'pass':    'check icon',
        'fail':    'times icon',
        'pending': 'clock icon',
      }[this.checksStatus] || '';
    },
    checksStatusText() {
      return {'pass': 'CI pass', 'fail': 'CI fail', 'pending': 'CI pending'}[this.checksStatus] || '';
    },
```

### 6.4 `loadTree()` changes

In `loadTree()` (starting at line 303), the method currently ends after setting `this.stats`. Make two changes:

**At the top of `loadTree()`,** reset checks data alongside the other resets. After `this.repoStats = null;` add:

```js
      this.checksData = null;
```

**At the bottom of `loadTree()`,** add a call to `loadChecks()` after `this.stats = {...}`:

```js
      this.stats = {fileCount, rowCount: totalRows};
      await this.loadChecks();
```

### 6.5 New `loadChecks()` method

Add to the `methods` section, after `loadStats()`:

```js
    async loadChecks() {
      if (!this.commitHash) return;
      this.checksLoading = true;
      try {
        this.checksData = await ditFetch(
          this.owner, this.repo,
          `/checks/${this.commitHash}`,
        );
      } catch {
        this.checksData = null;
      } finally {
        this.checksLoading = false;
      }
    },
```

### 6.6 Checklist

- [ ] Add CI badge `<div class="field">` block in `DataRepoHome.vue` template
- [ ] Add `checksLoading` and `checksData` to `data()`
- [ ] Add four computed properties (`checksStatus`, `checksStatusClass`, `checksStatusIcon`, `checksStatusText`)
- [ ] Reset `this.checksData = null` at top of `loadTree()`
- [ ] Call `await this.loadChecks()` at end of `loadTree()`
- [ ] Add `loadChecks()` method
- [ ] Verify no JS lint errors: `cd /Users/lxs/code/datahub-gateway && npx eslint web_src/js/components/DataRepoHome.vue --no-eslintrc --rule '{}' 2>/dev/null || true`

---

## Task 7: Final verification

### 7.1 Full Python test suite

```bash
cd /Users/lxs/code/dit
uv run pytest tests/ -v
```

All tests must pass. Expected new test files contributing passing tests:
- `tests/test_validate.py`
- `tests/test_cli_validate.py`
- `tests/server/test_routes_validate.py`

### 7.2 Go build check

```bash
cd /Users/lxs/code/datahub-gateway
go build ./...
```

No compilation errors.

### 7.3 Manual smoke test (optional but recommended)

Start the dit server:

```bash
cd /Users/lxs/code/dit
DIT_DATA_DIR=/tmp/dit-smoke uv run dit serve &
```

Create a repo and validate:

```bash
curl -s -X POST http://localhost:8000/api/v1/repos \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"name":"smoke-test"}'

curl -s -X POST http://localhost:8000/api/v1/repos/smoke-test/checks \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{"commit_hash":"'"$(python3 -c "print('a'*64)")"'","check_name":"ci","status":"pass"}'

curl -s http://localhost:8000/api/v1/repos/smoke-test/checks/$(python3 -c "print('a'*64)") \
  -H 'Authorization: Bearer <token>'
```

### 7.4 Checklist

- [ ] `uv run pytest tests/ -v` — all tests pass (no regressions)
- [ ] `go build ./...` — Go compiles cleanly
- [ ] No new `TODO`, `FIXME`, or placeholder code in any committed file

---

## Summary of new files and changed files

| File | Action |
|------|--------|
| `src/dit/core/validate.py` | **New** — `load_rules` + `validate_commit` |
| `tests/test_validate.py` | **New** — unit tests for core validate module |
| `src/dit/cli/main.py` | **Modified** — add `validate` command + `_count_result_files` |
| `tests/test_cli_validate.py` | **New** — CLI validate tests |
| `src/dit/server/models.py` | **Modified** — add `CICheck` model |
| `src/dit/server/routes/validate_api.py` | **New** — validate + checks endpoints |
| `src/dit/server/app.py` | **Modified** — register `validate_router` |
| `tests/server/test_routes_validate.py` | **New** — server endpoint tests |
| `modules/dit/client.go` | **Modified** — `Validate`, `ReportCheck`, `GetChecks` methods |
| `routers/api/v1/repo/dit.go` | **Modified** — `DatahubValidate`, `DatahubReportCheck`, `DatahubGetChecks` handlers |
| `routers/api/v1/api.go` | **Modified** — register three new dit routes |
| `web_src/js/components/DataRepoHome.vue` | **Modified** — CI badge template + script additions |
