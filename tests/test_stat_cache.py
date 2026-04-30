"""Tests for stat cache."""
import time
from pathlib import Path


from dit.core.stat_cache import StatCache


class TestStatCache:
    def test_cache_miss_on_empty(self, tmp_path: Path):
        cache = StatCache(tmp_path / ".dit" / "stat-cache")
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"a":1}\n')
        assert cache.get_manifest_hash("data.jsonl", fp) is None

    def test_cache_hit_after_update(self, tmp_path: Path):
        cache = StatCache(tmp_path / ".dit" / "stat-cache")
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"a":1}\n')
        cache.update("data.jsonl", fp, "abc123")
        assert cache.get_manifest_hash("data.jsonl", fp) == "abc123"

    def test_cache_miss_after_file_modified(self, tmp_path: Path):
        cache = StatCache(tmp_path / ".dit" / "stat-cache")
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"a":1}\n')
        cache.update("data.jsonl", fp, "abc123")
        # Modify the file (changes mtime and/or size)
        time.sleep(0.01)
        fp.write_text('{"a":1}\n{"b":2}\n')
        assert cache.get_manifest_hash("data.jsonl", fp) is None

    def test_cache_miss_after_invalidate(self, tmp_path: Path):
        cache = StatCache(tmp_path / ".dit" / "stat-cache")
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"a":1}\n')
        cache.update("data.jsonl", fp, "abc123")
        cache.invalidate("data.jsonl")
        assert cache.get_manifest_hash("data.jsonl", fp) is None

    def test_clear_removes_all(self, tmp_path: Path):
        cache = StatCache(tmp_path / ".dit" / "stat-cache")
        fp1 = tmp_path / "a.jsonl"
        fp2 = tmp_path / "b.jsonl"
        fp1.write_text('{"a":1}\n')
        fp2.write_text('{"b":2}\n')
        cache.update("a.jsonl", fp1, "hash_a")
        cache.update("b.jsonl", fp2, "hash_b")
        cache.clear()
        assert cache.get_manifest_hash("a.jsonl", fp1) is None
        assert cache.get_manifest_hash("b.jsonl", fp2) is None

    def test_corrupt_cache_file_returns_miss(self, tmp_path: Path):
        cache_path = tmp_path / ".dit" / "stat-cache"
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("not valid json{{{")
        cache = StatCache(cache_path)
        fp = tmp_path / "data.jsonl"
        fp.write_text('{"a":1}\n')
        assert cache.get_manifest_hash("data.jsonl", fp) is None
