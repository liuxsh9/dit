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
