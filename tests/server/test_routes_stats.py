import json
import asyncio
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    Sidecar, SidecarEntry,
    serialize_commit, serialize_manifest, serialize_sidecar,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_sidecars(
    client: AsyncClient,
    tmp_path: Path,
    repo: str = "stats-repo",
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    row = json.dumps({"instruction": "hello", "response": "world"})
    rh = store.write("rows", row.encode("utf-8"))
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))
    sc = Sidecar(
        manifest_hash=mh,
        entries=[SidecarEntry(row_hash=rh, char_count=40, token_estimate=10, field_count=2, lang="en")],
    )
    sc_hash = store.write("sidecars", serialize_sidecar(sc))

    eval_row = json.dumps({"q": "hi"})
    eval_rh = store.write("rows", eval_row.encode("utf-8"))
    eval_mh = store.write("manifests", serialize_manifest(Manifest(entries=[ManifestEntry(row_hash=eval_rh, query_fingerprint=None)])))

    tree_entries = {
        "train.jsonl": ("manifest", mh, sc_hash),
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
class TestStatsEndpoint:
    async def test_stats_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        assert resp.status_code == 200

    async def test_stats_response_has_commit_hash(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert data["commit_hash"] == commit_hash

    async def test_stats_response_has_files_and_totals(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert "files" in data
        assert "totals" in data

    async def test_stats_file_with_sidecar_has_stats(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        files_by_path = {f["path"]: f for f in data["files"]}
        train = files_by_path["train.jsonl"]
        assert train["has_sidecar"] is True
        assert train["row_count"] == 1
        assert train["token_estimate"] == 10

    async def test_stats_file_without_sidecar_has_null_fields(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        files_by_path = {f["path"]: f for f in data["files"]}
        eval_f = files_by_path["eval.jsonl"]
        assert eval_f["has_sidecar"] is False
        assert eval_f["row_count"] == 1

    async def test_stats_totals_files_with_sidecar(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}")
        data = resp.json()
        assert data["totals"]["file_count"] == 2
        assert data["totals"]["files_with_sidecar"] == 1
        assert data["totals"]["row_count"] == 2

    async def test_stats_path_filter(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/stats-repo/stats/{commit_hash}",
            params={"path": "train.jsonl"},
        )
        data = resp.json()
        assert len(data["files"]) == 1
        assert data["files"][0]["path"] == "train.jsonl"

    async def test_stats_can_skip_exact_size_for_fast_row_counts(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(
            f"/api/v1/repos/stats-repo/stats/{commit_hash}",
            params={"path": "train.jsonl", "include_size": "false"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["files"][0]["path"] == "train.jsonl"
        assert data["files"][0]["row_count"] == 1
        assert data["files"][0]["size_bytes"] is None
        assert data["totals"]["size_bytes"] is None

    async def test_stats_commit_not_found_returns_404(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_sidecars(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/stats-repo/stats/{'a' * 64}")
        assert resp.status_code == 404

    async def test_stats_repo_not_found_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/stats/{'a' * 64}")
        assert resp.status_code == 404

    async def test_stats_does_not_block_other_requests(self, client: AsyncClient, tmp_path: Path, monkeypatch):
        import threading

        store, commit_hash = await _create_repo_with_sidecars(client, tmp_path)

        # Event that is set when the slow function starts executing,
        # proving it is still running when the manifest request completes.
        slow_started = threading.Event()
        slow_finish = threading.Event()

        def slow_repo_stats(*_args, **_kwargs):
            slow_started.set()
            slow_finish.wait(timeout=5)
            return {
                "commit_hash": commit_hash,
                "files": [],
                "totals": {"file_count": 0, "files_with_sidecar": 0},
            }

        monkeypatch.setattr("dit.core.stats.repo_stats", slow_repo_stats)

        stats_task = asyncio.create_task(client.get(f"/api/v1/repos/stats-repo/stats/{commit_hash}"))
        # Give the event loop a moment to dispatch the stats request to the thread.
        await asyncio.sleep(0.05)
        assert slow_started.is_set(), "slow_repo_stats should have started in a thread"

        # While slow_repo_stats is still blocked, the manifest endpoint must respond.
        manifest_resp = await client.get(
            f"/api/v1/repos/stats-repo/manifest/{commit_hash}/train.jsonl"
            "?offset=0&limit=1"
        )
        assert manifest_resp.status_code == 200

        # Let the slow function finish so the stats task can complete.
        slow_finish.set()
        stats_resp = await stats_task
        assert stats_resp.status_code == 200
