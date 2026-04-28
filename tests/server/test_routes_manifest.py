import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_manifest_repo(client, tmp_path, n_rows: int = 10):
    resp = await client.post("/api/v1/repos", json={"name": "manifest-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "manifest-repo" / "objects")

    entries = [
        ManifestEntry(row_hash=f"{i:064x}", query_fingerprint=f"q{i}")
        for i in range(n_rows)
    ]
    manifest = Manifest(entries=entries)
    m_hash = store.write("manifests", serialize_manifest(manifest))

    staged = {"train/data.jsonl": ("manifest", m_hash)}
    tree_hash = build_nested_tree(store, staged)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash, m_hash, entries


class TestManifestRoute:
    async def test_full_manifest(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=5)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["entries"]) == 5
        assert data["offset"] == 0
        assert data["limit"] == 5

    async def test_pagination_offset(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=10)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
            "?offset=5&limit=3"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["offset"] == 5
        assert data["limit"] == 3
        assert len(data["entries"]) == 3
        assert data["entries"][0]["row_hash"] == f"{5:064x}"

    async def test_pagination_limit_clamp(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=5)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
            "?offset=0&limit=1000"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 5

    async def test_commit_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "manifest-repo2"})
        resp = await client.get(
            f"/api/v1/repos/manifest-repo2/manifest/{'a' * 64}/data.jsonl"
        )
        assert resp.status_code == 404

    async def test_path_not_manifest(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=3)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train"
        )
        assert resp.status_code == 404
