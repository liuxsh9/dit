import json
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree
from dit.core.hash import canonical_json, row_hash


async def _setup_diff_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "diff-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "diff-repo" / "objects")

    def make_row(content: str) -> ManifestEntry:
        row = {"messages": [{"role": "user", "content": content}]}
        canon = canonical_json(row)
        rh = row_hash(row)
        store.write("rows", canon)
        return ManifestEntry(row_hash=rh, query_fingerprint=None)

    row_a = make_row("hello")
    row_b = make_row("world")
    row_c = make_row("new")

    m_old = Manifest(entries=[row_a, row_b])
    m_old_hash = store.write("manifests", serialize_manifest(m_old))

    m_new = Manifest(entries=[row_b, row_c])
    m_new_hash = store.write("manifests", serialize_manifest(m_new))

    staged_old = {"data.jsonl": ("manifest", m_old_hash)}
    staged_new = {"data.jsonl": ("manifest", m_new_hash)}

    tree_old_hash = build_nested_tree(store, staged_old)
    tree_new_hash = build_nested_tree(store, staged_new)

    c_old = Commit(tree_hash=tree_old_hash, parent_hashes=[], author="t", message="old", timestamp=1000)
    h_old = store.write("commits", serialize_commit(c_old))

    c_new = Commit(tree_hash=tree_new_hash, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
    h_new = store.write("commits", serialize_commit(c_new))

    return store, h_old, h_new, row_a, row_b, row_c


class TestDiffApi:
    async def test_diff_between_commits(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "data.jsonl"
        assert f["added"] >= 1
        assert f["removed"] >= 1

    async def test_diff_per_file_rows(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "include_rows": True,
                "path": "data.jsonl",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        f = data["files"][0]
        assert "added_rows" in f
        assert "removed_rows" in f
        added_hashes = {r["row_hash"] for r in f["added_rows"]}
        removed_hashes = {r["row_hash"] for r in f["removed_rows"]}
        assert row_c.row_hash in added_hashes
        assert row_a.row_hash in removed_hashes

    async def test_diff_commit_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "diff-repo2"})
        resp = await client.post(
            "/api/v1/repos/diff-repo2/diff",
            json={"old_commit": "a" * 64, "new_commit": "b" * 64},
        )
        assert resp.status_code == 404

    async def test_diff_no_changes(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_old},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["files"] == []
