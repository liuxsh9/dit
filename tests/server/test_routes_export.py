import csv
import io
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_data(client: AsyncClient, tmp_path: Path, repo: str = "export-repo"):
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    rows = [
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        json.dumps({"messages": [{"role": "user", "content": "world"}]}),
    ]
    row_hashes = [store.write("rows", r.encode("utf-8")) for r in rows]
    entries = [ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
    manifest = Manifest(entries=entries)
    mh = store.write("manifests", serialize_manifest(manifest))

    tree_entries = {"train.jsonl": ("manifest", mh, None)}
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
    )
    return store, commit_hash


@pytest.mark.asyncio
class TestExportEndpoint:
    async def test_export_jsonl_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl")
        assert resp.status_code == 200

    async def test_export_jsonl_content_type(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl")
        assert "ndjson" in resp.headers["content-type"]

    async def test_export_jsonl_body_is_valid_ndjson(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl")
        lines = [l for l in resp.text.splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "messages" in obj

    async def test_export_csv_format(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl",
            params={"format": "csv"},
        )
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 2
        assert "messages" in rows[0]

    async def test_export_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_data(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/export-repo/export/{'z' * 64}/train.jsonl")
        assert resp.status_code == 404

    async def test_export_file_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/export-repo/export/{commit_hash}/nonexistent.jsonl")
        assert resp.status_code == 404

    async def test_export_repo_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/export/{'a' * 64}/train.jsonl")
        assert resp.status_code == 404

    async def test_export_invalid_format(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl",
            params={"format": "parquet"},
        )
        assert resp.status_code == 400
