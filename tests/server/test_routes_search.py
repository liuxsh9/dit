# tests/server/test_routes_search.py
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


async def _create_repo_with_rows(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "search-repo",
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    rows_train = [
        json.dumps({"instruction": "实现一个LRU缓存，支持get和put操作", "response": "好的"}),
        json.dumps({"instruction": "LRU缓存淘汰策略", "response": "正确"}),
        json.dumps({"instruction": "quicksort", "response": "ok"}),
    ]
    train_entries = []
    for r in rows_train:
        rh = store.write("rows", r.encode("utf-8"))
        train_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    train_manifest = Manifest(entries=train_entries)
    train_mh = store.write("manifests", serialize_manifest(train_manifest))

    rows_eval = [
        json.dumps({"messages": [{"role": "user", "content": "LRU缓存时间复杂度"}], "response": "O(1)"}),
    ]
    eval_entries = []
    for r in rows_eval:
        rh = store.write("rows", r.encode("utf-8"))
        eval_entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    eval_manifest = Manifest(entries=eval_entries)
    eval_mh = store.write("manifests", serialize_manifest(eval_manifest))

    tree_entries = {
        "train.jsonl": ("manifest", train_mh, None),
        "eval.jsonl": ("manifest", eval_mh, None),
    }
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
class TestSearchEndpoint:
    async def test_search_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        assert resp.status_code == 200

    async def test_search_response_has_required_keys(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        data = resp.json()
        assert "commit_hash" in data
        assert "query" in data
        assert "matches" in data
        assert "total_scanned" in data
        assert "limit_reached" in data

    async def test_search_returns_matches(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        data = resp.json()
        assert len(data["matches"]) > 0

    async def test_search_match_has_expected_keys(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU"},
        )
        m = resp.json()["matches"][0]
        assert "file" in m
        assert "row_index" in m
        assert "row_hash" in m
        assert "content" in m
        assert "highlight" in m

    async def test_search_no_matches_returns_empty_list(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "zzznomatch"},
        )
        assert resp.status_code == 200
        assert resp.json()["matches"] == []

    async def test_search_with_file_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "file": "train.jsonl"},
        )
        data = resp.json()
        files = {m["file"] for m in data["matches"]}
        assert "eval.jsonl" not in files

    async def test_search_with_field_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "field": "messages[0].content"},
        )
        data = resp.json()
        assert len(data["matches"]) == 1
        assert data["matches"][0]["file"] == "eval.jsonl"

    async def test_search_with_limit(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash, "query": "LRU", "limit": 1},
        )
        data = resp.json()
        assert len(data["matches"]) == 1
        assert data["limit_reached"] is True

    async def test_search_with_branch_ref(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": "heads/main", "query": "LRU"},
        )
        assert resp.status_code == 200

    async def test_search_missing_query_returns_422(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 422

    async def test_search_bad_ref_returns_404(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/search-repo/search",
            json={"ref": "heads/nonexistent", "query": "LRU"},
        )
        assert resp.status_code == 404

    async def test_search_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/search",
            json={"ref": "heads/main", "query": "LRU"},
        )
        assert resp.status_code == 404
