import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Tree,
    serialize_commit, serialize_tree,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_log_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "log-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "log-repo" / "objects")

    tree_hash = build_nested_tree(store, {})

    c1 = Commit(tree_hash=tree_hash, parent_hashes=[], author="a", message="first", timestamp=1000)
    h1 = store.write("commits", serialize_commit(c1))

    c2 = Commit(tree_hash=tree_hash, parent_hashes=[h1], author="a", message="second", timestamp=2000)
    h2 = store.write("commits", serialize_commit(c2))

    c3 = Commit(tree_hash=tree_hash, parent_hashes=[h2], author="b", message="third", timestamp=3000)
    h3 = store.write("commits", serialize_commit(c3))

    await client.post(
        "/api/v1/repos/log-repo/refs/heads/main",
        json={"old": None, "new": h3},
    )
    return store, h1, h2, h3


class TestLogRoute:
    async def test_default_log(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main")
        assert resp.status_code == 200
        data = resp.json()
        assert "commits" in data
        assert len(data["commits"]) == 3
        assert data["commits"][0]["commit_hash"] == h3
        assert data["commits"][1]["commit_hash"] == h2
        assert data["commits"][2]["commit_hash"] == h1

    async def test_log_pagination(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 2
        assert data["commits"][0]["commit_hash"] == h3

    async def test_log_offset(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=10&offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 1
        assert data["commits"][0]["commit_hash"] == h1

    async def test_log_commit_fields(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=1")
        assert resp.status_code == 200
        commit = resp.json()["commits"][0]
        assert "commit_hash" in commit
        assert "author" in commit
        assert "message" in commit
        assert "timestamp" in commit
        assert "parent_hashes" in commit

    async def test_log_ref_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "log-repo2"})
        resp = await client.get("/api/v1/repos/log-repo2/log?ref=heads/nosuchbranch")
        assert resp.status_code == 404

    async def test_log_repo_not_found(self, client, tmp_path):
        resp = await client.get("/api/v1/repos/no-such-repo/log?ref=heads/main")
        assert resp.status_code == 404
