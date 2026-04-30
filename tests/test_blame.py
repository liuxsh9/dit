"""Tests for dit.core.blame module."""
import json
import time

from dit.core.blame import blame_file, row_history
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore


def _write_row(store: ObjectStore, content: dict) -> str:
    """Write a row object and return its hash."""
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(
    store: ObjectStore,
    files: dict[str, list[dict]],
    parent_hashes: list[str] | None = None,
    author: str = "alice",
    message: str = "commit",
    timestamp: int | None = None,
) -> str:
    """Build a commit with the given file→rows mapping. Returns commit hash."""
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_bytes = serialize_manifest(manifest)
        m_hash = store.write("manifests", m_bytes)
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))

    tree = Tree(entries=tree_entries)
    t_bytes = serialize_tree(tree)
    t_hash = store.write("trees", t_bytes)

    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author=author,
        message=message,
        timestamp=timestamp or int(time.time()),
    )
    c_bytes = serialize_commit(c)
    return store.write("commits", c_bytes)


class TestBlameFile:
    """Tests for blame_file()."""

    def test_single_commit_all_rows_blamed_to_it(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        rows = [
            {"text": "hello", "label": "pos"},
            {"text": "world", "label": "neg"},
        ]
        c1 = _make_commit(store, {"train.jsonl": rows}, author="alice", timestamp=1000)

        result = blame_file(store, c1, "train.jsonl")

        assert result["file"] == "train.jsonl"
        assert result["commit_hash"] == c1
        assert len(result["entries"]) == 2
        for entry in result["entries"]:
            assert entry["commit_hash"] == c1
            assert entry["author"] == "alice"
            assert entry["timestamp"] == 1000
        assert result["summary"]["total_rows"] == 2
        assert result["summary"]["unique_commits"] == 1
        assert result["summary"]["unique_authors"] == 1

    def test_two_commits_new_rows_blamed_to_second(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "hello", "label": "pos"}
        row_b = {"text": "world", "label": "neg"}

        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")

        entries = result["entries"]
        assert len(entries) == 2
        # row_a was in c1
        assert entries[0]["commit_hash"] == c1
        assert entries[0]["author"] == "alice"
        # row_b was added in c2
        assert entries[1]["commit_hash"] == c2
        assert entries[1]["author"] == "bob"

    def test_refreshed_row_blamed_to_refresh_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        # Same query, different response → refresh
        row_v1 = {"messages": [{"role": "user", "content": "hi"}], "response": "old"}
        row_v2 = {"messages": [{"role": "user", "content": "hi"}], "response": "new"}

        c1 = _make_commit(store, {"train.jsonl": [row_v1]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_v2]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 1
        # The refreshed row should be blamed to c2
        assert entries[0]["commit_hash"] == c2
        assert entries[0]["author"] == "bob"

    def test_unchanged_rows_blamed_to_original_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "stable", "label": "pos"}
        row_b = {"text": "also stable", "label": "neg"}

        c1 = _make_commit(store, {"train.jsonl": [row_a, row_b]}, author="alice", timestamp=1000)
        # c2 has same rows — no changes to train.jsonl
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = blame_file(store, c2, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 2
        # Both rows should be blamed to c1 (original)
        for entry in entries:
            assert entry["commit_hash"] == c1
            assert entry["author"] == "alice"

    def test_file_not_found_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"train.jsonl": [{"a": 1}]}, timestamp=1000)

        import pytest
        with pytest.raises(FileNotFoundError):
            blame_file(store, c1, "nonexistent.jsonl")

    def test_commit_not_found_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")

        import pytest
        with pytest.raises(FileNotFoundError):
            blame_file(store, "0" * 64, "train.jsonl")

    def test_three_commits_mixed_attribution(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_a = {"text": "alpha", "label": "a"}
        row_b = {"text": "beta", "label": "b"}
        row_c = {"text": "gamma", "label": "c"}

        c1 = _make_commit(store, {"train.jsonl": [row_a]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_a, row_b]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )
        c3 = _make_commit(
            store, {"train.jsonl": [row_a, row_b, row_c]},
            parent_hashes=[c2], author="carol", timestamp=3000,
        )

        result = blame_file(store, c3, "train.jsonl")
        entries = result["entries"]
        assert len(entries) == 3
        assert entries[0]["commit_hash"] == c1  # row_a from alice
        assert entries[1]["commit_hash"] == c2  # row_b from bob
        assert entries[2]["commit_hash"] == c3  # row_c from carol

    def test_content_preview_present(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "hello world this is a test", "label": "positive"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, timestamp=1000)

        result = blame_file(store, c1, "train.jsonl")
        assert "content_preview" in result["entries"][0]
        assert len(result["entries"][0]["content_preview"]) > 0


class TestRowHistory:
    """Tests for row_history()."""

    def test_single_commit_shows_added_event(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "hello", "label": "pos"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", timestamp=1000)

        result = row_history(store, c1, "train.jsonl", 0)
        assert result["row_index"] == 0
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "added"
        assert result["events"][0]["commit_hash"] == c1

    def test_refresh_shows_both_events(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row_v1 = {"messages": [{"role": "user", "content": "hi"}], "response": "old"}
        row_v2 = {"messages": [{"role": "user", "content": "hi"}], "response": "new"}

        c1 = _make_commit(store, {"train.jsonl": [row_v1]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row_v2]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = row_history(store, c2, "train.jsonl", 0)
        events = result["events"]
        assert len(events) == 2
        # Newest first
        assert events[0]["event"] == "refresh"
        assert events[0]["commit_hash"] == c2
        assert events[1]["event"] == "added"
        assert events[1]["commit_hash"] == c1

    def test_row_index_out_of_range_raises(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"train.jsonl": [{"a": 1}]}, timestamp=1000)

        import pytest
        with pytest.raises(IndexError):
            row_history(store, c1, "train.jsonl", 5)

    def test_row_without_query_fingerprint_tracks_by_hash(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        row = {"text": "simple row", "label": "x"}
        c1 = _make_commit(store, {"train.jsonl": [row]}, author="alice", timestamp=1000)
        c2 = _make_commit(
            store, {"train.jsonl": [row]},
            parent_hashes=[c1], author="bob", timestamp=2000,
        )

        result = row_history(store, c2, "train.jsonl", 0)
        assert result["query_fingerprint"] is None
        assert len(result["events"]) == 1
        assert result["events"][0]["event"] == "added"
        assert result["events"][0]["commit_hash"] == c1
