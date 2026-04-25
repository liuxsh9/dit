import json
from pathlib import Path

import pytest

from dit.core.workspace import find_jsonl_files, build_manifest_for_file, materialize_file
from dit.core.objects import deserialize_manifest
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
        assert len(row_data) == 4
        for entry in manifest.entries:
            assert len(entry.row_hash) == 64
            assert entry.raw_row_hash is not None

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

    def test_captures_raw_row_text_for_materialization(self, tmp_repo: Path):
        fp = tmp_repo / "test.jsonl"
        fp.write_text('{"role":"user","content":"hi"}\n')
        manifest, row_data = build_manifest_for_file(fp)
        assert manifest.entries[0].raw_row_hash is not None
        assert manifest.entries[0].raw_row_hash in row_data


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


def test_materialize_preserves_original_row_text_when_available(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    original_text = (
        '{"messages":[{"role":"user","content":"hello"},{"role":"assistant","content":"hi"}]}\n'
    )
    src.write_text(original_text)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for hash_hex, data in row_data.items():
        obj_type = "row_text" if data.endswith(b"\n") else "rows"
        store.write(obj_type, data)

    dest_root = tmp_path / "clone"
    materialize_file(dest_root, "data.jsonl", manifest, store)

    dest = dest_root / "data.jsonl"
    assert dest.read_text() == original_text


def test_materialize_falls_back_to_canonical_json_when_raw_text_missing(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    src.write_text('{"role":"user","content":"hello"}\n')

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, row_data = build_manifest_for_file(src)
    for hash_hex, data in row_data.items():
        if not data.endswith(b"\n"):
            store.write("rows", data)

    dest_root = tmp_path / "clone"
    materialize_file(dest_root, "data.jsonl", manifest, store)

    dest = dest_root / "data.jsonl"
    assert json.loads(dest.read_text().strip()) == {"role": "user", "content": "hello"}
    assert dest.read_text() != src.read_text()


def test_materialize_missing_row_raises(tmp_path: Path) -> None:
    rows = [{"messages": [{"role": "user", "content": "x"}]}]
    src = tmp_path / "data.jsonl"
    _write_jsonl(src, rows)

    store = ObjectStore(tmp_path / ".dit" / "objects")
    manifest, _row_data = build_manifest_for_file(src)
    with pytest.raises(KeyError):
        materialize_file(tmp_path / "clone", "data.jsonl", manifest, store)


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
