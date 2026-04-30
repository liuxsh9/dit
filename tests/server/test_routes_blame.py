"""Tests for the blame API endpoint."""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash


async def _setup_blame_repo(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "blame-repo",
) -> tuple[ObjectStore, str, str]:
    """Create a repo with two commits for blame testing.

    Returns (store, c1_hash, c2_hash).
    Commit 1: row_a only (author=alice)
    Commit 2: row_a + row_b (author=bob)
    """
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    row_a = {"text": "alpha", "label": "a"}
    row_b = {"text": "beta", "label": "b"}

    rh_a = compute_row_hash(row_a)
    rh_b = compute_row_hash(row_b)
    store.write("rows", json.dumps(row_a, separators=(",", ":"), sort_keys=True).encode())
    store.write("rows", json.dumps(row_b, separators=(",", ":"), sort_keys=True).encode())

    # Commit 1: just row_a
    m1 = Manifest(entries=[ManifestEntry(row_hash=rh_a, query_fingerprint=None)])
    m1_hash = store.write("manifests", serialize_manifest(m1))
    t1 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m1_hash)])
    t1_hash = store.write("trees", serialize_tree(t1))
    c1 = Commit(
        tree_hash=t1_hash, parent_hashes=[],
        author="alice", message="c1", timestamp=1000,
    )
    c1_hash = store.write("commits", serialize_commit(c1))

    # Commit 2: row_a + row_b
    m2 = Manifest(entries=[
        ManifestEntry(row_hash=rh_a, query_fingerprint=None),
        ManifestEntry(row_hash=rh_b, query_fingerprint=None),
    ])
    m2_hash = store.write("manifests", serialize_manifest(m2))
    t2 = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m2_hash)])
    t2_hash = store.write("trees", serialize_tree(t2))
    c2 = Commit(
        tree_hash=t2_hash, parent_hashes=[c1_hash],
        author="bob", message="c2", timestamp=2000,
    )
    c2_hash = store.write("commits", serialize_commit(c2))

    return store, c1_hash, c2_hash


@pytest.mark.asyncio
class TestBlameEndpoint:

    async def test_blame_full_file(self, client: AsyncClient, tmp_path: Path):
        store, c1_hash, c2_hash = await _setup_blame_repo(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/blame-repo/blame/{c2_hash}/train.jsonl",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["file"] == "train.jsonl"
        assert len(body["entries"]) == 2
        assert body["entries"][0]["commit_hash"] == c1_hash
        assert body["entries"][0]["author"] == "alice"
        assert body["entries"][1]["commit_hash"] == c2_hash
        assert body["entries"][1]["author"] == "bob"

    async def test_blame_summary(self, client: AsyncClient, tmp_path: Path):
        store, c1_hash, c2_hash = await _setup_blame_repo(
            client, tmp_path, repo="blame-summary",
        )

        resp = await client.get(
            f"/api/v1/repos/blame-summary/blame/{c2_hash}/train.jsonl",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["total_rows"] == 2
        assert body["summary"]["unique_commits"] == 2
        assert body["summary"]["unique_authors"] == 2

    async def test_blame_row_history(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-rh"})
        assert resp.status_code == 201

        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "blame-rh" / "objects")

        row = {"text": "hello", "label": "pos"}
        rh = compute_row_hash(row)
        store.write("rows", json.dumps(row, separators=(",", ":"), sort_keys=True).encode())

        m = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(
            tree_hash=t_hash, parent_hashes=[],
            author="alice", message="init", timestamp=1000,
        )
        c_hash = store.write("commits", serialize_commit(c))

        resp = await client.get(
            f"/api/v1/repos/blame-rh/blame/{c_hash}/data.jsonl?row=0",
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["row_index"] == 0
        assert len(body["events"]) == 1
        assert body["events"][0]["event"] == "added"
        assert body["events"][0]["author"] == "alice"

    async def test_blame_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-404"})
        assert resp.status_code == 201

        resp = await client.get(
            f"/api/v1/repos/blame-404/blame/{'0' * 64}/train.jsonl",
        )
        assert resp.status_code == 404

    async def test_blame_file_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-nf"})
        assert resp.status_code == 201

        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "blame-nf" / "objects")
        m = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="other.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(
            tree_hash=t_hash, parent_hashes=[],
            author="x", message="x", timestamp=1,
        )
        c_hash = store.write("commits", serialize_commit(c))

        resp = await client.get(
            f"/api/v1/repos/blame-nf/blame/{c_hash}/missing.jsonl",
        )
        assert resp.status_code == 404

    async def test_blame_row_out_of_range(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-oor"})
        assert resp.status_code == 201

        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "blame-oor" / "objects")
        row = {"a": 1}
        rh = compute_row_hash(row)
        store.write("rows", json.dumps(row, separators=(",", ":"), sort_keys=True).encode())
        m = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="f.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(
            tree_hash=t_hash, parent_hashes=[],
            author="x", message="x", timestamp=1,
        )
        c_hash = store.write("commits", serialize_commit(c))

        resp = await client.get(
            f"/api/v1/repos/blame-oor/blame/{c_hash}/f.jsonl?row=99",
        )
        assert resp.status_code == 400

    async def test_blame_requires_auth(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-auth"})
        assert resp.status_code == 201

        # Make request without Authorization header
        from httpx import AsyncClient as RawClient
        # Extract the app from the existing client transport
        transport = client._transport
        async with RawClient(transport=transport, base_url="http://test") as raw:
            resp = await raw.get(
                f"/api/v1/repos/blame-auth/blame/{'a' * 64}/f.jsonl",
            )
        assert resp.status_code in (401, 403)

    async def test_blame_repo_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(
            f"/api/v1/repos/nonexistent-repo/blame/{'a' * 64}/f.jsonl",
        )
        assert resp.status_code == 404

    async def test_blame_content_preview_present(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos", json={"name": "blame-preview"})
        assert resp.status_code == 201

        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "blame-preview" / "objects")
        row = {"text": "hello world this is a test", "label": "positive"}
        rh = compute_row_hash(row)
        store.write("rows", json.dumps(row, separators=(",", ":"), sort_keys=True).encode())
        m = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(m))
        t = Tree(entries=[TreeEntry(name="p.jsonl", obj_type="manifest", obj_hash=m_hash)])
        t_hash = store.write("trees", serialize_tree(t))
        c = Commit(
            tree_hash=t_hash, parent_hashes=[],
            author="x", message="x", timestamp=1,
        )
        c_hash = store.write("commits", serialize_commit(c))

        resp = await client.get(
            f"/api/v1/repos/blame-preview/blame/{c_hash}/p.jsonl",
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "content_preview" in body["entries"][0]
        assert len(body["entries"][0]["content_preview"]) > 0
