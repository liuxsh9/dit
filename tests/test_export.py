import pytest
from dit.core.objects import Sidecar, SidecarEntry
from dit.core.sidecar import sidecar_summary


def _make_sidecar() -> Sidecar:
    entries = [
        SidecarEntry(row_hash="a" * 64, char_count=100, token_estimate=25, field_count=3, lang="en"),
        SidecarEntry(row_hash="b" * 64, char_count=200, token_estimate=50, field_count=5, lang="zh"),
        SidecarEntry(row_hash="c" * 64, char_count=150, token_estimate=37, field_count=4, lang="en"),
    ]
    return Sidecar(manifest_hash="m" * 64, entries=entries)


class TestSidecarSummary:
    def test_returns_correct_row_count(self):
        result = sidecar_summary(_make_sidecar())
        assert result["row_count"] == 3

    def test_returns_correct_char_count(self):
        result = sidecar_summary(_make_sidecar())
        assert result["char_count"] == 450

    def test_returns_correct_token_estimate(self):
        result = sidecar_summary(_make_sidecar())
        assert result["token_estimate"] == 112

    def test_returns_avg_fields(self):
        result = sidecar_summary(_make_sidecar())
        assert result["avg_fields"] == pytest.approx(4.0, rel=0.01)

    def test_lang_distribution(self):
        result = sidecar_summary(_make_sidecar())
        assert result["lang_distribution"] == {"en": 2, "zh": 1}

    def test_empty_sidecar(self):
        empty = Sidecar(manifest_hash="m" * 64, entries=[])
        result = sidecar_summary(empty)
        assert result == {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }


import json
import time
from pathlib import Path

from dit.core.export import export_commit
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


def _build_repo(tmp_path: Path) -> tuple[ObjectStore, str]:
    store = ObjectStore(tmp_path / "objects")

    rows_a = [
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        json.dumps({"messages": [{"role": "user", "content": "world"}]}),
    ]
    rows_b = [
        json.dumps({"messages": [{"role": "user", "content": "foo"}]}),
    ]

    def _write_manifest(rows: list[str]) -> str:
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        return store.write("manifests", serialize_manifest(manifest))

    mh_a = _write_manifest(rows_a)
    mh_b = _write_manifest(rows_b)

    tree_entries = {
        "train.jsonl": ("manifest", mh_a, None),
        "eval.jsonl": ("manifest", mh_b, None),
    }
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


class TestExportCommitJsonl:
    def test_exports_all_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out)

        assert (out / "train.jsonl").exists()
        assert (out / "eval.jsonl").exists()
        assert len(report) == 2

    def test_report_contains_path_and_rows(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out)

        paths = {r["path"] for r in report}
        assert "train.jsonl" in paths
        assert "eval.jsonl" in paths

        by_path = {r["path"]: r for r in report}
        assert by_path["train.jsonl"]["rows"] == 2
        assert by_path["eval.jsonl"]["rows"] == 1

    def test_jsonl_content_is_valid_json_per_line(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out)

        lines = (out / "train.jsonl").read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "messages" in obj

    def test_file_filter_exports_single_file(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out, file_filter="train.jsonl")

        assert (out / "train.jsonl").exists()
        assert not (out / "eval.jsonl").exists()
        assert len(report) == 1

    def test_unknown_file_filter_raises(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        with pytest.raises(FileNotFoundError, match="not found"):
            export_commit(store, commit_hash, out, file_filter="missing.jsonl")


class TestExportCommitCsv:
    def test_csv_has_header(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        lines = (out / "train.csv").read_text().splitlines()
        assert len(lines) >= 2
        assert "messages" in lines[0]

    def test_csv_row_count_matches_manifest(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        lines = (out / "train.csv").read_text().splitlines()
        assert len(lines) == 3  # header + 2 data rows
        assert report[0]["rows"] == 2

    def test_csv_nested_values_are_json_strings(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        import csv as _csv
        with (out / "train.csv").open() as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)
        val = rows[0]["messages"]
        parsed = json.loads(val)
        assert isinstance(parsed, list)

    def test_invalid_format_raises(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        with pytest.raises(ValueError, match="Unknown format"):
            export_commit(store, commit_hash, out, fmt="parquet")


def _build_repo_with_sidecar(tmp_path: Path) -> tuple[ObjectStore, str]:
    store = ObjectStore(tmp_path / "objects")

    row = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
    rh = store.write("rows", row.encode("utf-8"))

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    sc_entries = [SidecarEntry(row_hash=rh, char_count=len(row), token_estimate=len(row) // 4, field_count=1, lang="en")]
    sidecar = Sidecar(manifest_hash=mh, entries=sc_entries)
    sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

    tree_entries = {"train.jsonl": ("manifest", mh, sc_hash)}
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


class TestExportIncludeMeta:
    def test_meta_file_created_when_flag_set(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        assert (out / "train.jsonl").exists()
        assert (out / "train.jsonl.meta.json").exists()

    def test_meta_file_not_created_by_default(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out)

        assert not (out / "train.jsonl.meta.json").exists()

    def test_meta_file_content(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        meta = json.loads((out / "train.jsonl.meta.json").read_text())
        assert meta["file"] == "train.jsonl"
        assert "manifest_hash" in meta
        assert "sidecar_hash" in meta
        assert meta["row_count"] == 1
        assert meta["char_count"] > 0
        assert "lang_distribution" in meta

    def test_meta_skipped_when_no_sidecar(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        assert (out / "train.jsonl").exists()
        assert not (out / "train.jsonl.meta.json").exists()
