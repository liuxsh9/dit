import json
from pathlib import Path

from dit.core.workspace import find_jsonl_files, build_manifest_for_file
from dit.core.objects import deserialize_manifest


class TestFindJsonlFiles:
    def test_finds_jsonl(self, tmp_repo: Path):
        (tmp_repo / "a.jsonl").write_text('{"x":1}\n')
        (tmp_repo / "b.txt").write_text("not jsonl")
        (tmp_repo / "sub").mkdir()
        (tmp_repo / "sub" / "c.jsonl").write_text('{"y":2}\n')
        files = find_jsonl_files(tmp_repo)
        rel_paths = sorted(str(f.relative_to(tmp_repo)) for f in files)
        assert rel_paths == ["a.jsonl", "sub/c.jsonl"]

    def test_ignores_datahub_dir(self, tmp_repo: Path):
        (tmp_repo / ".datahub").mkdir()
        (tmp_repo / ".datahub" / "internal.jsonl").write_text('{"z":1}\n')
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
