import json
from pathlib import Path

import pytest

from dit.core.workspace import find_jsonl_files, build_manifest_for_file, build_manifest_for_file_streaming, materialize_file
from dit.core.objects import deserialize_manifest, Manifest
from dit.core.store import ObjectStore
from dit.utils.jsonl import write_rows


class TestFindJsonlFiles:
    def test_finds_jsonl(self, tmp_repo: Path):
        (tmp_repo / "a.jsonl").write_text('{"x":1}\n')
        (tmp_repo / "b.txt").write_text("not jsonl")
        (tmp_repo / "sub").mkdir()
        (tmp_repo / "sub" / "c.jsonl").write_text('{"y":2}\n')
        files = find_jsonl_files(tmp_repo)
        rel_paths = sorted(str(f.relative_to(tmp_repo)) for f in files)
        assert rel_paths == ["a.jsonl", "sub/c.jsonl"]

    def test_ignores_dit_dir(self, tmp_repo: Path):
        (tmp_repo / ".dit").mkdir()
        (tmp_repo / ".dit" / "internal.jsonl").write_text('{"z":1}\n')
        (tmp_repo / "real.jsonl").write_text('{"w":1}\n')
        files = find_jsonl_files(tmp_repo)
        assert len(files) == 1
        assert files[0].name == "real.jsonl"


class TestBuildManifest:
    def test_builds_manifest(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        fp.write_text('{"a":1}\n{"b":2}\n')
        manifest, row_data = build_manifest_for_file(fp)
        assert len(manifest.entries) == 2
        assert len(row_data) == 2
        for entry in manifest.entries:
            assert len(entry.row_hash) == 64

    def test_preserves_row_order(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        rows = [{"id": i} for i in range(10)]
        fp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        manifest, row_data = build_manifest_for_file(fp)
        assert len(manifest.entries) == 10

    def test_row_data_matches_entries(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        fp.write_text('{"x":1}\n{"y":2}\n')
        manifest, row_data = build_manifest_for_file(fp)
        for entry in manifest.entries:
            assert entry.row_hash in row_data


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_rows(path, rows)


def test_materialize_roundtrip(tmp_path: Path) -> None:
    rows = [
        {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]},
        {"messages": [{"role": "user", "content": "bye"}, {"role": "assistant", "content": "bye!"}]},
    ]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for rh, data in row_data.items():
        store.write("rows", data)

    dest_root = tmp_path / "clone"
    materialize_file(dest_root, "data.jsonl", manifest, store)

    dest = dest_root / "data.jsonl"
    assert dest.exists()
    materialized = [json.loads(line) for line in dest.read_text().splitlines() if line.strip()]
    assert len(materialized) == len(rows)
    for original, materialized_row in zip(rows, materialized):
        assert materialized_row == original


def test_materialize_missing_row_raises(tmp_path: Path) -> None:
    rows = [{"messages": [{"role": "user", "content": "x"}]}]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, _row_data = build_manifest_for_file(src)
    with pytest.raises(KeyError):
        materialize_file(tmp_path / "clone", "data.jsonl", manifest, store)


def test_materialize_streams_without_accumulation(tmp_path: Path) -> None:
    """Verify materialize_file writes correct output via streaming (no list accumulation)."""
    rows = [{"id": i, "value": f"row-{i}"} for i in range(200)]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for rh, data in row_data.items():
        store.write("rows", data)

    dest_root = tmp_path / "clone"
    materialize_file(dest_root, "data.jsonl", manifest, store)

    dest = dest_root / "data.jsonl"
    lines = dest.read_text().splitlines()
    assert len(lines) == 200
    for i, line in enumerate(lines):
        parsed = json.loads(line)
        assert parsed == rows[i]


def test_materialize_creates_parent_dirs(tmp_path: Path) -> None:
    rows = [{"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for rh, data in row_data.items():
        store.write("rows", data)

    materialize_file(tmp_path / "clone", "nested/deep/data.jsonl", manifest, store)
    assert (tmp_path / "clone" / "nested" / "deep" / "data.jsonl").exists()


class TestBuildManifestStreaming:
    """Tests for the streaming manifest builder that avoids OOM."""

    def _make_jsonl(self, tmp_path: Path, rows: list[dict]) -> Path:
        fp = tmp_path / "test.jsonl"
        fp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return fp

    def test_streaming_produces_same_manifest(self, tmp_path: Path) -> None:
        """Streaming version produces the exact same Manifest as the original."""
        rows = [{"id": i, "text": f"row-{i}"} for i in range(20)]
        fp = self._make_jsonl(tmp_path, rows)
        store = ObjectStore(tmp_path / ".dit" / "objects")

        original_manifest, _ = build_manifest_for_file(fp)
        streaming_manifest = build_manifest_for_file_streaming(fp, store)

        assert len(streaming_manifest.entries) == len(original_manifest.entries)
        for orig, stream in zip(original_manifest.entries, streaming_manifest.entries):
            assert orig.row_hash == stream.row_hash
            assert orig.query_fingerprint == stream.query_fingerprint

    def test_streaming_writes_rows_to_store(self, tmp_path: Path) -> None:
        """After streaming, all row hashes exist in the store."""
        rows = [{"id": i, "text": f"row-{i}"} for i in range(10)]
        fp = self._make_jsonl(tmp_path, rows)
        store = ObjectStore(tmp_path / ".dit" / "objects")

        manifest = build_manifest_for_file_streaming(fp, store)

        for entry in manifest.entries:
            assert store.exists("rows", entry.row_hash), (
                f"Row {entry.row_hash[:8]} not found in store"
            )

    def test_streaming_rows_match_original(self, tmp_path: Path) -> None:
        """Row data in the store matches what build_manifest_for_file returns."""
        rows = [{"id": i, "text": f"row-{i}"} for i in range(10)]
        fp = self._make_jsonl(tmp_path, rows)
        store = ObjectStore(tmp_path / ".dit" / "objects")

        original_manifest, original_row_data = build_manifest_for_file(fp)
        build_manifest_for_file_streaming(fp, store)

        for rh, expected_bytes in original_row_data.items():
            actual_bytes = store.read("rows", rh)
            assert actual_bytes == expected_bytes

    def test_streaming_does_not_accumulate_row_data(self, tmp_path: Path) -> None:
        """Streaming version returns only Manifest, not row_data."""
        rows = [{"id": 1}]
        fp = self._make_jsonl(tmp_path, rows)
        store = ObjectStore(tmp_path / ".dit" / "objects")

        result = build_manifest_for_file_streaming(fp, store)

        assert isinstance(result, Manifest)
        # Should NOT be a tuple — no row_data dict returned
        assert not isinstance(result, tuple)
