# tests/server/test_routes_validate.py
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
    repo: str = "validate-repo",
    rows_by_file: dict | None = None,
) -> tuple[ObjectStore, str]:
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    if rows_by_file is None:
        rows_by_file = {
            "train.jsonl": [
                json.dumps({"instruction": "hello", "response": "world"}),
                json.dumps({"instruction": "foo", "response": "bar"}),
            ],
            "eval.jsonl": [
                json.dumps({"instruction": "test", "response": "ok"}),
            ],
        }

    tree_entries: dict = {}
    for filename, rows in rows_by_file.items():
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        mh = store.write("manifests", serialize_manifest(manifest))
        tree_entries[filename] = ("manifest", mh, None)

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
class TestValidateEndpoint:
    async def test_validate_pass_returns_200(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 200

    async def test_validate_pass_body(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": commit_hash},
        )
        data = resp.json()
        assert data["status"] == "pass"
        assert data["violations"] == []
        assert "checked_rows" in data

    async def test_validate_with_branch_ref(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": "heads/main"},
        )
        assert resp.status_code == 200

    async def test_validate_bad_ref_returns_404(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={"ref": "heads/nonexistent"},
        )
        assert resp.status_code == 404

    async def test_validate_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/validate",
            json={"ref": "heads/main"},
        )
        assert resp.status_code == 404

    async def test_validate_default_ref_is_heads_main(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_rows(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/validate-repo/validate",
            json={},
        )
        assert resp.status_code == 200

    async def test_validate_always_200_even_on_fail(self, client: AsyncClient, tmp_path: Path):
        """HTTP status 200 regardless of pass/fail — status in body."""
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path,
            repo="validate-repo-fail",
            rows_by_file={
                "train.jsonl": [json.dumps({"x": "y"})],
            },
        )
        # No rules file in the store, but we can still test the endpoint returns 200
        resp = await client.post(
            "/api/v1/repos/validate-repo-fail/validate",
            json={"ref": commit_hash},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestChecksEndpoints:
    async def test_report_check_returns_201(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo"
        )
        resp = await client.post(
            "/api/v1/repos/checks-repo/checks",
            json={
                "commit_hash": commit_hash,
                "check_name": "data-quality-ci",
                "status": "pass",
                "details": {"passed": 3, "failed": 0},
            },
        )
        assert resp.status_code == 201

    async def test_report_check_body_has_required_keys(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo2"
        )
        resp = await client.post(
            "/api/v1/repos/checks-repo2/checks",
            json={
                "commit_hash": commit_hash,
                "check_name": "data-quality-ci",
                "status": "pass",
            },
        )
        data = resp.json()
        assert "id" in data
        assert "repo_id" in data
        assert "commit_hash" in data
        assert "check_name" in data
        assert "status" in data
        assert "created_at" in data
        assert "updated_at" in data

    async def test_report_check_upsert(self, client: AsyncClient, tmp_path: Path):
        """Second POST with same (repo, commit, check_name) updates, not duplicates."""
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo3"
        )
        payload = {
            "commit_hash": commit_hash,
            "check_name": "data-quality-ci",
            "status": "pending",
        }
        r1 = await client.post("/api/v1/repos/checks-repo3/checks", json=payload)
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        payload["status"] = "pass"
        r2 = await client.post("/api/v1/repos/checks-repo3/checks", json=payload)
        assert r2.status_code == 201
        data2 = r2.json()
        assert data2["id"] == id1          # same row, not a duplicate
        assert data2["status"] == "pass"

    async def test_get_checks_returns_200(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo4"
        )
        await client.post(
            "/api/v1/repos/checks-repo4/checks",
            json={"commit_hash": commit_hash, "check_name": "ci", "status": "pass"},
        )
        resp = await client.get(f"/api/v1/repos/checks-repo4/checks/{commit_hash}")
        assert resp.status_code == 200

    async def test_get_checks_body_structure(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo5"
        )
        await client.post(
            "/api/v1/repos/checks-repo5/checks",
            json={"commit_hash": commit_hash, "check_name": "ci", "status": "pass"},
        )
        resp = await client.get(f"/api/v1/repos/checks-repo5/checks/{commit_hash}")
        data = resp.json()
        assert "commit_hash" in data
        assert "checks" in data
        assert len(data["checks"]) == 1
        c = data["checks"][0]
        assert c["check_name"] == "ci"
        assert c["status"] == "pass"

    async def test_get_checks_empty_when_none(self, client: AsyncClient, tmp_path: Path):
        _, commit_hash = await _create_repo_with_rows(
            client, tmp_path, repo="checks-repo6"
        )
        resp = await client.get(f"/api/v1/repos/checks-repo6/checks/{commit_hash}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checks"] == []

    async def test_report_check_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/checks",
            json={"commit_hash": "a" * 64, "check_name": "ci", "status": "pass"},
        )
        assert resp.status_code == 404

    async def test_get_checks_unknown_repo_returns_404(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/checks/{'a' * 64}")
        assert resp.status_code == 404
