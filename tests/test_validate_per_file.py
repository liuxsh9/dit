# tests/test_validate_per_file.py
"""Tests for per_file validation rule overrides."""
import json
import os
import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.validate import _resolve_rules_for_file, load_rules, validate_commit


# ── helpers ──────────────────────────────────────────────────────────────────

def _default_rules(**overrides):
    base = {
        "required_fields": [],
        "forbidden_keywords": [],
        "max_row_chars": None,
        "min_row_chars": None,
        "per_file": {},
    }
    base.update(overrides)
    return base


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


# ── _resolve_rules_for_file unit tests ──────────────────────────────────────

class TestResolveRulesForFile:
    def test_no_per_file_returns_global(self):
        rules = _default_rules(required_fields=["id", "text"])
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["required_fields"] == ["id", "text"]
        assert resolved["forbidden_keywords"] == []
        assert resolved["max_row_chars"] is None

    def test_per_file_replaces_required_fields(self):
        rules = _default_rules(
            required_fields=["id", "text"],
            per_file={"legacy/*": {"required_fields": ["data"]}},
        )
        resolved = _resolve_rules_for_file(rules, "legacy/old.jsonl")
        assert resolved["required_fields"] == ["data"]

    def test_per_file_null_clears_required_fields(self):
        rules = _default_rules(
            required_fields=["id", "text"],
            per_file={"legacy/*": {"required_fields": None}},
        )
        resolved = _resolve_rules_for_file(rules, "legacy/old.jsonl")
        assert resolved["required_fields"] == []

    def test_per_file_null_clears_forbidden_keywords(self):
        rules = _default_rules(
            forbidden_keywords=["OpenAI", "GPT"],
            per_file={"*.jsonl": {"forbidden_keywords": None}},
        )
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["forbidden_keywords"] == []

    def test_per_file_null_clears_max_row_chars(self):
        rules = _default_rules(
            max_row_chars=1000,
            per_file={"big/*": {"max_row_chars": None}},
        )
        resolved = _resolve_rules_for_file(rules, "big/huge.jsonl")
        assert resolved["max_row_chars"] is None

    def test_per_file_null_clears_min_row_chars(self):
        rules = _default_rules(
            min_row_chars=50,
            per_file={"tiny/*": {"min_row_chars": None}},
        )
        resolved = _resolve_rules_for_file(rules, "tiny/small.jsonl")
        assert resolved["min_row_chars"] is None

    def test_non_matching_file_keeps_global(self):
        rules = _default_rules(
            required_fields=["id", "text"],
            per_file={"legacy/*": {"required_fields": ["data"]}},
        )
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["required_fields"] == ["id", "text"]

    def test_glob_star_matches_extension(self):
        rules = _default_rules(
            required_fields=["id"],
            per_file={"*.csv": {"required_fields": ["col1"]}},
        )
        resolved = _resolve_rules_for_file(rules, "data.csv")
        assert resolved["required_fields"] == ["col1"]
        # .jsonl should not match
        resolved2 = _resolve_rules_for_file(rules, "data.jsonl")
        assert resolved2["required_fields"] == ["id"]

    def test_last_match_wins(self):
        rules = _default_rules(
            required_fields=["global_field"],
            per_file={
                "*.jsonl": {"required_fields": ["first_match"]},
                "train.*": {"required_fields": ["second_match"]},
            },
        )
        # "train.jsonl" matches both patterns; second should win
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["required_fields"] == ["second_match"]

    def test_last_match_wins_three_patterns(self):
        rules = _default_rules(
            max_row_chars=100,
            per_file={
                "*": {"max_row_chars": 200},
                "*.jsonl": {"max_row_chars": 300},
                "train.jsonl": {"max_row_chars": 400},
            },
        )
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["max_row_chars"] == 400

    def test_empty_overrides_no_change(self):
        rules = _default_rules(
            required_fields=["id"],
            per_file={"*.jsonl": {}},
        )
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["required_fields"] == ["id"]

    def test_partial_override_only_changes_specified_key(self):
        rules = _default_rules(
            required_fields=["id"],
            forbidden_keywords=["secret"],
            max_row_chars=500,
            per_file={"*.jsonl": {"max_row_chars": 1000}},
        )
        resolved = _resolve_rules_for_file(rules, "train.jsonl")
        assert resolved["required_fields"] == ["id"]
        assert resolved["forbidden_keywords"] == ["secret"]
        assert resolved["max_row_chars"] == 1000


# ── load_rules per_file parsing ─────────────────────────────────────────────

class TestLoadRulesPerFile:
    def test_no_per_file_section_returns_empty_dict(self, tmp_path: Path):
        (tmp_path / ".ditvalidate.yaml").write_text(
            "required_fields:\n  - id\n"
        )
        rules = load_rules(tmp_path)
        assert rules["per_file"] == {}

    def test_per_file_parsed_correctly(self, tmp_path: Path):
        yaml_text = (
            "required_fields:\n  - id\n"
            "per_file:\n"
            "  'legacy/*':\n"
            "    required_fields:\n"
            "      - data\n"
        )
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        rules = load_rules(tmp_path)
        assert "legacy/*" in rules["per_file"]
        assert rules["per_file"]["legacy/*"]["required_fields"] == ["data"]

    def test_per_file_null_override_stored_as_none(self, tmp_path: Path):
        yaml_text = (
            "required_fields:\n  - id\n"
            "per_file:\n"
            "  'legacy/*':\n"
            "    required_fields: null\n"
        )
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        rules = load_rules(tmp_path)
        assert rules["per_file"]["legacy/*"]["required_fields"] is None

    def test_per_file_invalid_non_dict_raises(self, tmp_path: Path):
        yaml_text = "per_file:\n  - pattern1\n  - pattern2\n"
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        with pytest.raises(ValueError, match="per_file must be a mapping"):
            load_rules(tmp_path)

    def test_per_file_pattern_value_non_dict_raises(self, tmp_path: Path):
        yaml_text = (
            "per_file:\n"
            "  'legacy/*': not_a_dict\n"
        )
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        with pytest.raises(ValueError, match="per_file\\['legacy/\\*'\\] must be a mapping"):
            load_rules(tmp_path)

    def test_per_file_invalid_required_fields_type_raises(self, tmp_path: Path):
        yaml_text = (
            "per_file:\n"
            "  '*.jsonl':\n"
            "    required_fields: not_a_list\n"
        )
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        with pytest.raises(ValueError, match="required_fields must be a list or null"):
            load_rules(tmp_path)

    def test_per_file_invalid_max_row_chars_raises(self, tmp_path: Path):
        yaml_text = (
            "per_file:\n"
            "  '*.jsonl':\n"
            "    max_row_chars: -5\n"
        )
        (tmp_path / ".ditvalidate.yaml").write_text(yaml_text)
        with pytest.raises(ValueError, match="max_row_chars must be a positive integer"):
            load_rules(tmp_path)

    def test_backward_compat_no_per_file_key(self, tmp_path: Path):
        """Config without per_file works exactly as before."""
        (tmp_path / ".ditvalidate.yaml").write_text(
            "required_fields:\n  - id\nforbidden_keywords:\n  - secret\n"
            "max_row_chars: 1000\nmin_row_chars: 10\n"
        )
        rules = load_rules(tmp_path)
        assert rules["required_fields"] == ["id"]
        assert rules["forbidden_keywords"] == ["secret"]
        assert rules["max_row_chars"] == 1000
        assert rules["min_row_chars"] == 10
        assert rules["per_file"] == {}

    def test_backward_compat_no_config_file(self, tmp_path: Path):
        rules = load_rules(tmp_path)
        assert rules["per_file"] == {}
        assert rules["required_fields"] == []


# ── validate_commit with per_file ───────────────────────────────────────────

class TestValidateCommitPerFile:
    def test_per_file_override_replaces_required_fields(self, tmp_path: Path):
        """Global requires [id, text], but legacy/* only requires [data].
        legacy/old.jsonl has {data: x} -> pass for that file.
        train.jsonl has {id: 1, text: hi} -> pass for global rules."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"id": "1", "text": "hi"})],
            "legacy/old.jsonl": [json.dumps({"data": "x"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id", "text"],
            per_file={"legacy/*": {"required_fields": ["data"]}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"
        assert result["checked_rows"] == 2

    def test_per_file_override_causes_violation_on_matching_file(self, tmp_path: Path):
        """legacy/old.jsonl missing the per-file required field 'data'."""
        rows_by_file = {
            "legacy/old.jsonl": [json.dumps({"id": "1"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"legacy/*": {"required_fields": ["data"]}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        assert len(result["violations"]) == 1
        assert result["violations"][0]["rule"] == "required_fields"
        assert "data" in result["violations"][0]["detail"]

    def test_per_file_null_clears_required_fields_for_matching(self, tmp_path: Path):
        """Global requires [id], but legacy/* clears it.
        legacy/old.jsonl has no 'id' -> still passes."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"id": "1"})],
            "legacy/old.jsonl": [json.dumps({"anything": "goes"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"legacy/*": {"required_fields": None}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_per_file_null_clears_but_global_still_enforced(self, tmp_path: Path):
        """Global requires [id]. legacy/* clears it.
        train.jsonl missing 'id' -> fail (global still applies)."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"no_id": "oops"})],
            "legacy/old.jsonl": [json.dumps({"anything": "goes"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"legacy/*": {"required_fields": None}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        assert len(result["violations"]) == 1
        assert result["violations"][0]["file"] == "train.jsonl"

    def test_per_file_glob_star_jsonl(self, tmp_path: Path):
        """*.jsonl pattern matches top-level jsonl files."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"x": "y"})],
            "data.csv": [json.dumps({"x": "y"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"*.jsonl": {"required_fields": None}},
        )
        result = validate_commit(store, commit_hash, rules)
        # train.jsonl: per_file clears required_fields -> pass
        # data.csv: global required_fields=["id"] -> fail (missing id)
        assert result["status"] == "fail"
        assert len(result["violations"]) == 1
        assert result["violations"][0]["file"] == "data.csv"

    def test_per_file_does_not_affect_non_matching(self, tmp_path: Path):
        """per_file for legacy/* should not affect train.jsonl."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"x": "y"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"legacy/*": {"required_fields": None}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        assert result["violations"][0]["file"] == "train.jsonl"

    def test_per_file_empty_overrides_no_change(self, tmp_path: Path):
        """Empty per_file override dict should not change global rules."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"x": "y"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            required_fields=["id"],
            per_file={"*.jsonl": {}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        assert result["violations"][0]["rule"] == "required_fields"

    def test_multiple_patterns_last_wins(self, tmp_path: Path):
        """Two patterns match train.jsonl; the last one's max_row_chars wins."""
        short_row = json.dumps({"x": "a" * 50})
        rows_by_file = {"train.jsonl": [short_row]}
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            max_row_chars=10,  # global: would fail
            per_file={
                "*": {"max_row_chars": 20},       # first match: still fail
                "*.jsonl": {"max_row_chars": 9999},  # last match: pass
            },
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"

    def test_per_file_forbidden_keywords_override(self, tmp_path: Path):
        """Per-file clears forbidden_keywords for legacy files."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"text": "OpenAI is great"})],
            "legacy/old.jsonl": [json.dumps({"text": "OpenAI is great"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            forbidden_keywords=["OpenAI"],
            per_file={"legacy/*": {"forbidden_keywords": None}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        # Only train.jsonl should have the violation
        assert len(result["violations"]) == 1
        assert result["violations"][0]["file"] == "train.jsonl"

    def test_per_file_max_row_chars_override(self, tmp_path: Path):
        """Per-file raises max_row_chars for big files."""
        big_row = json.dumps({"x": "a" * 500})
        rows_by_file = {
            "train.jsonl": [big_row],
            "big/huge.jsonl": [big_row],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)
        rules = _default_rules(
            max_row_chars=100,
            per_file={"big/*": {"max_row_chars": 9999}},
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "fail"
        # Only train.jsonl should fail
        files_with_violations = {v["file"] for v in result["violations"]}
        assert "train.jsonl" in files_with_violations
        assert "big/huge.jsonl" not in files_with_violations


# ── CLI integration ─────────────────────────────────────────────────────────

class TestCLIPerFileDisplay:
    def test_validate_shows_per_file_pattern_count(self, tmp_path: Path):
        from typer.testing import CliRunner
        from dit.cli.main import app
        from dit.core.refs import RefStore

        os.chdir(tmp_path)
        cli_runner = CliRunner()
        cli_runner.invoke(app, ["init"])

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        (tmp_path / ".ditvalidate.yaml").write_text(
            "required_fields:\n  - id\n"
            "per_file:\n"
            "  'legacy/*':\n"
            "    required_fields: null\n"
            "  '*.csv':\n"
            "    required_fields:\n"
            "      - col1\n"
        )

        rows = [json.dumps({"id": "1"})]
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries = {"train.jsonl": ("manifest", mh, None)}
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

        result = cli_runner.invoke(app, ["validate"])
        assert "per_file=2 pattern(s)" in result.stdout

    def test_validate_no_per_file_no_display(self, tmp_path: Path):
        from typer.testing import CliRunner
        from dit.cli.main import app
        from dit.core.refs import RefStore

        os.chdir(tmp_path)
        cli_runner = CliRunner()
        cli_runner.invoke(app, ["init"])

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)

        (tmp_path / ".ditvalidate.yaml").write_text(
            "required_fields:\n  - id\n"
        )

        rows = [json.dumps({"id": "1"})]
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries = {"train.jsonl": ("manifest", mh, None)}
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

        result = cli_runner.invoke(app, ["validate"])
        assert "per_file=" not in result.stdout


# ── Stress test ─────────────────────────────────────────────────────────────

class TestPerFileStress:
    def test_many_patterns_many_files(self, tmp_path: Path):
        """20+ per_file patterns, 12 files, verify correct rule resolution."""
        # Build 12 files across different directories
        rows_by_file = {}
        for i in range(4):
            rows_by_file[f"train_{i}.jsonl"] = [
                json.dumps({"id": str(i), "text": f"row {i}"})
            ]
        for i in range(4):
            rows_by_file[f"legacy/file_{i}.jsonl"] = [
                json.dumps({"data": f"legacy {i}"})
            ]
        for i in range(4):
            rows_by_file[f"special/file_{i}.csv"] = [
                json.dumps({"col1": f"val {i}"})
            ]

        store, commit_hash = _build_commit(tmp_path, rows_by_file)

        # Build 25 per_file patterns
        per_file = {}
        # Patterns 1-5: generic catch-alls (overridden by later patterns)
        for i in range(5):
            per_file[f"pattern_no_match_{i}"] = {"max_row_chars": 10}
        # Patterns 6-10: legacy files get relaxed rules
        per_file["legacy/*"] = {"required_fields": ["data"]}
        for i in range(4):
            per_file[f"legacy/extra_{i}*"] = {"max_row_chars": 9999}
        # Patterns 11-15: special csv files
        per_file["special/*.csv"] = {"required_fields": ["col1"]}
        for i in range(4):
            per_file[f"special/extra_{i}*"] = {"max_row_chars": 9999}
        # Patterns 16-20: train files keep global rules
        for i in range(5):
            per_file[f"no_match_dir_{i}/*"] = {"required_fields": ["nope"]}
        # Patterns 21-25: more non-matching
        for i in range(5):
            per_file[f"another_no_match_{i}/*"] = {"forbidden_keywords": ["x"]}

        rules = _default_rules(
            required_fields=["id", "text"],
            per_file=per_file,
        )

        result = validate_commit(store, commit_hash, rules)

        # train_*.jsonl: global rules [id, text] -> all have id+text -> pass
        train_violations = [v for v in result["violations"] if v["file"].startswith("train_")]
        assert train_violations == []

        # legacy/*.jsonl: per_file requires [data] -> all have data -> pass
        legacy_violations = [v for v in result["violations"] if v["file"].startswith("legacy/")]
        assert legacy_violations == []

        # special/*.csv: per_file requires [col1] -> all have col1 -> pass
        special_violations = [v for v in result["violations"] if v["file"].startswith("special/")]
        assert special_violations == []

        assert result["status"] == "pass"
        assert result["checked_rows"] == 12

    def test_stress_conflicting_patterns_last_wins(self, tmp_path: Path):
        """Many overlapping patterns; verify last-match-wins semantics."""
        rows_by_file = {
            "train.jsonl": [json.dumps({"x": "y"})],
        }
        store, commit_hash = _build_commit(tmp_path, rows_by_file)

        # Build 20 patterns that all match train.jsonl, each setting a
        # different max_row_chars. The last one (max_row_chars=9999) should win.
        per_file = {}
        for i in range(20):
            per_file[f"{'?' * (i + 1)}*"] = {"max_row_chars": i + 1}
        # Final pattern: exact match, generous limit
        per_file["train.jsonl"] = {"max_row_chars": 9999}

        rules = _default_rules(
            max_row_chars=1,  # global: would fail
            per_file=per_file,
        )
        result = validate_commit(store, commit_hash, rules)
        assert result["status"] == "pass"
