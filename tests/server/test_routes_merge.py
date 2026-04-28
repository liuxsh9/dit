"""Tests for server merge-preview and merge API routes."""
import json
import time

import pytest

from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Sidecar,
    SidecarEntry,
    Tree,
    TreeEntry,
    deserialize_commit,
    deserialize_tree,
    serialize_commit,
    serialize_manifest,
    serialize_sidecar,
    serialize_tree,
)
from dit.core.store import ObjectStore


async def _setup_diverged_repo(client, tmp_path):
    """Create a repo with two diverged branches on the server."""
    resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "test-repo" / "objects")

    BASE_ROW_HASH = "a" * 64
    MAIN_ROW_HASH = "b" * 64
    FEAT_ROW_HASH = "c" * 64

    # Create base commit
    base_row = ManifestEntry(row_hash=BASE_ROW_HASH, query_fingerprint="q1")
    base_m = Manifest(entries=[base_row])
    base_m_hash = store.write("manifests", serialize_manifest(base_m))

    base_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash)])
    base_tree_hash = store.write("trees", serialize_tree(base_tree))
    base_commit = Commit(tree_hash=base_tree_hash, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    base_hash = store.write("commits", serialize_commit(base_commit))

    # Create main commit (adds main_row)
    main_row = ManifestEntry(row_hash=MAIN_ROW_HASH, query_fingerprint="q2")
    main_m = Manifest(entries=[base_row, main_row])
    main_m_hash = store.write("manifests", serialize_manifest(main_m))

    main_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=main_m_hash)])
    main_tree_hash = store.write("trees", serialize_tree(main_tree))
    main_commit = Commit(tree_hash=main_tree_hash, parent_hashes=[base_hash], author="test", message="main change", timestamp=int(time.time()))
    main_hash = store.write("commits", serialize_commit(main_commit))

    # Create feature commit (adds feat_row — diverges from main)
    feat_row = ManifestEntry(row_hash=FEAT_ROW_HASH, query_fingerprint="q3")
    feat_m = Manifest(entries=[base_row, feat_row])
    feat_m_hash = store.write("manifests", serialize_manifest(feat_m))

    feat_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=feat_m_hash)])
    feat_tree_hash = store.write("trees", serialize_tree(feat_tree))
    feat_commit = Commit(tree_hash=feat_tree_hash, parent_hashes=[base_hash], author="test", message="feature change", timestamp=int(time.time()))
    feat_hash = store.write("commits", serialize_commit(feat_commit))

    return store, base_hash, main_hash, feat_hash


class TestMergePreview:
    async def test_mergeable(self, client, tmp_path):
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/merge-preview",
            json={"source_branch": "feature", "target_branch": "main"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mergeable"] is True
        assert data["conflicts"] == []

    async def test_branch_not_found(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
        resp = await client.post(
            "/api/v1/repos/test-repo/merge-preview",
            json={"source_branch": "nope", "target_branch": "main"},
        )
        assert resp.status_code == 404


class TestMerge:
    async def test_clean_merge(self, client, tmp_path):
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "commit_hash" in data
        assert len(data["commit_hash"]) == 64
        ref_resp = await client.get("/api/v1/repos/test-repo/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["commit_hash"]

    async def test_merge_branch_not_found(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "nope",
                "target_branch": "main",
                "message": "m",
                "author": "t",
            },
        )
        assert resp.status_code == 404


async def _setup_diverged_repo_named(client, tmp_path, repo_name: str):
    """Create a named repo with two diverged branches on the server."""
    resp = await client.post("/api/v1/repos", json={"name": repo_name})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo_name / "objects")

    BASE_ROW_HASH = "a" * 64
    MAIN_ROW_HASH = "b" * 64
    FEAT_ROW_HASH = "c" * 64

    base_row = ManifestEntry(row_hash=BASE_ROW_HASH, query_fingerprint="q1")
    base_m = Manifest(entries=[base_row])
    base_m_hash = store.write("manifests", serialize_manifest(base_m))

    base_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash)])
    base_tree_hash = store.write("trees", serialize_tree(base_tree))
    base_commit = Commit(tree_hash=base_tree_hash, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    base_hash = store.write("commits", serialize_commit(base_commit))

    main_row = ManifestEntry(row_hash=MAIN_ROW_HASH, query_fingerprint="q2")
    main_m = Manifest(entries=[base_row, main_row])
    main_m_hash = store.write("manifests", serialize_manifest(main_m))

    main_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=main_m_hash)])
    main_tree_hash = store.write("trees", serialize_tree(main_tree))
    main_commit = Commit(tree_hash=main_tree_hash, parent_hashes=[base_hash], author="test", message="main change", timestamp=int(time.time()))
    main_hash = store.write("commits", serialize_commit(main_commit))

    feat_row = ManifestEntry(row_hash=FEAT_ROW_HASH, query_fingerprint="q3")
    feat_m = Manifest(entries=[base_row, feat_row])
    feat_m_hash = store.write("manifests", serialize_manifest(feat_m))

    feat_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=feat_m_hash)])
    feat_tree_hash = store.write("trees", serialize_tree(feat_tree))
    feat_commit = Commit(tree_hash=feat_tree_hash, parent_hashes=[base_hash], author="test", message="feature change", timestamp=int(time.time()))
    feat_hash = store.write("commits", serialize_commit(feat_commit))

    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/main", json={"old": None, "new": main_hash})
    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/feature", json={"old": None, "new": feat_hash})

    return store, base_hash, main_hash, feat_hash


class TestMergeApprovalEnforcement:
    async def test_merge_blocked_when_approvals_insufficient(self, client, tmp_path):
        repo_name = "merge-approval-blocked"
        await _setup_diverged_repo_named(client, tmp_path, repo_name)

        # Add branch protection requiring 1 approval on main
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/branch-protection",
            json={"branch_pattern": "main", "required_approvals": 1},
        )
        assert resp.status_code == 201

        pr_resp = await client.post(
            f"/api/v1/repos/{repo_name}/pulls",
            json={"title": "Merge PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["pull_request_id"]

        # Attempt merge with a real PR but no approvals submitted
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature",
                "author": "tester",
                "pull_request_id": pr_id,
            },
        )
        assert resp.status_code == 403
        assert "approval" in resp.json()["detail"].lower()

    async def test_merge_allowed_when_approvals_met(self, client, tmp_path):
        repo_name = "merge-approval-pass"
        await _setup_diverged_repo_named(client, tmp_path, repo_name)

        # Add branch protection requiring 1 approval on main
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/branch-protection",
            json={"branch_pattern": "main", "required_approvals": 1},
        )
        assert resp.status_code == 201

        pr_resp = await client.post(
            f"/api/v1/repos/{repo_name}/pulls",
            json={"title": "Merge PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["pull_request_id"]

        # Submit 1 approval for a real PR
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201

        # Merge should succeed
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature",
                "author": "tester",
                "pull_request_id": pr_id,
            },
        )
        assert resp.status_code == 200

    async def test_merge_ignores_approvals_from_other_repos(self, client, tmp_path):
        approved_repo = "merge-approval-source"
        protected_repo = "merge-approval-target"
        await _setup_diverged_repo_named(client, tmp_path, approved_repo)
        await _setup_diverged_repo_named(client, tmp_path, protected_repo)

        await client.post(
            f"/api/v1/repos/{protected_repo}/branch-protection",
            json={"branch_pattern": "main", "required_approvals": 1},
        )

        approved_pr = await client.post(
            f"/api/v1/repos/{approved_repo}/pulls",
            json={"title": "Source PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert approved_pr.status_code == 201

        protected_pr = await client.post(
            f"/api/v1/repos/{protected_repo}/pulls",
            json={"title": "Target PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert protected_pr.status_code == 201
        protected_pr_id = protected_pr.json()["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/{approved_repo}/pulls/{approved_pr.json()['pull_request_id']}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201

        resp = await client.post(
            f"/api/v1/repos/{protected_repo}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature",
                "author": "tester",
                "pull_request_id": protected_pr_id,
            },
        )
        assert resp.status_code == 403
        assert "only 0 found" in resp.json()["detail"]

    async def test_merge_to_unprotected_branch_no_approvals_needed(self, client, tmp_path):
        repo_name = "merge-approval-noprotect"
        await _setup_diverged_repo_named(client, tmp_path, repo_name)

        # No branch protection rules — merge without pull_request_id should succeed
        resp = await client.post(
            f"/api/v1/repos/{repo_name}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature",
                "author": "tester",
            },
        )
        assert resp.status_code == 200


class TestMergeFastForwardCAS:
    """Test CAS protection on the fast-forward merge path."""

    async def _setup_ff_repo(self, client, tmp_path, repo_name: str):
        """Create a repo where feature is a direct descendant of main (fast-forward eligible)."""
        resp = await client.post("/api/v1/repos", json={"name": repo_name})
        assert resp.status_code == 201

        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / repo_name / "objects")

        BASE_ROW_HASH = "a" * 64
        FEAT_ROW_HASH = "c" * 64

        base_row = ManifestEntry(row_hash=BASE_ROW_HASH, query_fingerprint="q1")
        base_m = Manifest(entries=[base_row])
        base_m_hash = store.write("manifests", serialize_manifest(base_m))

        base_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash)])
        base_tree_hash = store.write("trees", serialize_tree(base_tree))
        base_commit = Commit(tree_hash=base_tree_hash, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
        base_hash = store.write("commits", serialize_commit(base_commit))

        feat_row = ManifestEntry(row_hash=FEAT_ROW_HASH, query_fingerprint="q3")
        feat_m = Manifest(entries=[base_row, feat_row])
        feat_m_hash = store.write("manifests", serialize_manifest(feat_m))

        feat_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=feat_m_hash)])
        feat_tree_hash = store.write("trees", serialize_tree(feat_tree))
        feat_commit = Commit(tree_hash=feat_tree_hash, parent_hashes=[base_hash], author="test", message="feature", timestamp=int(time.time()))
        feat_hash = store.write("commits", serialize_commit(feat_commit))

        await client.post(f"/api/v1/repos/{repo_name}/refs/heads/main", json={"old": None, "new": base_hash})
        await client.post(f"/api/v1/repos/{repo_name}/refs/heads/feature", json={"old": None, "new": feat_hash})

        return store, base_hash, feat_hash

    async def test_fast_forward_merge_succeeds(self, client, tmp_path):
        """Normal fast-forward merge should succeed and return fast_forward=True."""
        repo_name = "ff-cas-ok"
        store, base_hash, feat_hash = await self._setup_ff_repo(client, tmp_path, repo_name)

        resp = await client.post(
            f"/api/v1/repos/{repo_name}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "FF merge",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fast_forward"] is True
        assert data["commit_hash"] == feat_hash

    async def test_fast_forward_cas_returns_409_on_concurrent_update(self, client, tmp_path):
        """If target ref moved between resolve and CAS update, merge returns 409."""
        from unittest.mock import patch

        repo_name = "ff-cas-conflict"
        store, base_hash, feat_hash = await self._setup_ff_repo(client, tmp_path, repo_name)

        # Strategy: patch _resolve_branch so it returns a stale (fake) hash for
        # the target branch. find_merge_base is also patched to return that stale
        # hash (triggering the FF path). The CAS UPDATE will then fail because
        # the real ref in DB has target_hash=base_hash, not stale_hash.
        stale_hash = "f" * 64  # does not match base_hash in DB

        # Capture real function before patching
        from dit.server.routes.merge import _resolve_branch as real_resolve

        async def patched_resolve(session, repo_id, branch):
            if branch == "main":
                return stale_hash
            return await real_resolve(session, repo_id, branch)

        with patch("dit.server.routes.merge._resolve_branch", side_effect=patched_resolve):
            with patch("dit.core.merge_base.find_merge_base", return_value=stale_hash):
                resp = await client.post(
                    f"/api/v1/repos/{repo_name}/merge",
                    json={
                        "source_branch": "feature",
                        "target_branch": "main",
                        "message": "FF merge",
                        "author": "tester",
                    },
                )

        assert resp.status_code == 409
        assert "concurrently" in resp.json()["detail"]


async def _setup_diverged_repo_with_sidecars(client, tmp_path, repo_name: str):
    """Create a repo with two diverged branches where trees have sidecar_hash set."""
    resp = await client.post("/api/v1/repos", json={"name": repo_name})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo_name / "objects")

    BASE_ROW_HASH = "a" * 64
    MAIN_ROW_HASH = "b" * 64
    FEAT_ROW_HASH = "c" * 64

    # Create sidecar objects
    base_sidecar = Sidecar(
        manifest_hash="placeholder",
        entries=[SidecarEntry(row_hash=BASE_ROW_HASH, char_count=10, token_estimate=3, field_count=2, lang="en")],
    )
    base_sc_hash = store.write("sidecars", serialize_sidecar(base_sidecar))

    main_sidecar = Sidecar(
        manifest_hash="placeholder",
        entries=[SidecarEntry(row_hash=MAIN_ROW_HASH, char_count=20, token_estimate=5, field_count=3, lang="en")],
    )
    main_sc_hash = store.write("sidecars", serialize_sidecar(main_sidecar))

    feat_sidecar = Sidecar(
        manifest_hash="placeholder",
        entries=[SidecarEntry(row_hash=FEAT_ROW_HASH, char_count=30, token_estimate=7, field_count=4, lang="en")],
    )
    feat_sc_hash = store.write("sidecars", serialize_sidecar(feat_sidecar))

    # Base commit: data.jsonl with sidecar
    base_row = ManifestEntry(row_hash=BASE_ROW_HASH, query_fingerprint="q1")
    base_m = Manifest(entries=[base_row])
    base_m_hash = store.write("manifests", serialize_manifest(base_m))

    base_tree = Tree(entries=[
        TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash, sidecar_hash=base_sc_hash),
    ])
    base_tree_hash = store.write("trees", serialize_tree(base_tree))
    base_commit = Commit(tree_hash=base_tree_hash, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    base_hash = store.write("commits", serialize_commit(base_commit))

    # Main commit: data.jsonl updated (with main sidecar), shared.jsonl added (with feat sidecar reused)
    main_row = ManifestEntry(row_hash=MAIN_ROW_HASH, query_fingerprint="q2")
    main_m = Manifest(entries=[base_row, main_row])
    main_m_hash = store.write("manifests", serialize_manifest(main_m))

    main_tree = Tree(entries=[
        TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=main_m_hash, sidecar_hash=main_sc_hash),
    ])
    main_tree_hash = store.write("trees", serialize_tree(main_tree))
    main_commit = Commit(tree_hash=main_tree_hash, parent_hashes=[base_hash], author="test", message="main change", timestamp=int(time.time()))
    main_hash = store.write("commits", serialize_commit(main_commit))

    # Feature commit: adds new file feat.jsonl with sidecar (diverges from base, not main)
    feat_row = ManifestEntry(row_hash=FEAT_ROW_HASH, query_fingerprint="q3")
    feat_m = Manifest(entries=[feat_row])
    feat_m_hash = store.write("manifests", serialize_manifest(feat_m))

    feat_tree = Tree(entries=[
        TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash, sidecar_hash=base_sc_hash),
        TreeEntry(name="feat.jsonl", obj_type="manifest", obj_hash=feat_m_hash, sidecar_hash=feat_sc_hash),
    ])
    feat_tree_hash = store.write("trees", serialize_tree(feat_tree))
    feat_commit = Commit(tree_hash=feat_tree_hash, parent_hashes=[base_hash], author="test", message="feature change", timestamp=int(time.time()))
    feat_hash = store.write("commits", serialize_commit(feat_commit))

    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/main", json={"old": None, "new": main_hash})
    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/feature", json={"old": None, "new": feat_hash})

    return store, base_hash, main_hash, feat_hash, main_sc_hash, feat_sc_hash


class TestMergeSidecarPreservation:
    """Verify that sidecar_hash values survive three-way merge."""

    async def test_merge_preserves_sidecar_hash(self, client, tmp_path):
        """After a clean merge, the resulting commit tree must carry sidecar_hash for every file."""
        repo_name = "merge-sidecar"
        store, base_hash, main_hash, feat_hash, main_sc_hash, feat_sc_hash = (
            await _setup_diverged_repo_with_sidecars(client, tmp_path, repo_name)
        )

        resp = await client.post(
            f"/api/v1/repos/{repo_name}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        commit_hash = data["commit_hash"]

        # Read the merge commit and inspect its tree
        commit_obj = deserialize_commit(store.read("commits", commit_hash))
        tree_obj = deserialize_tree(store.read("trees", commit_obj.tree_hash))
        entries_by_name = {e.name: e for e in tree_obj.entries}

        # data.jsonl should have main's sidecar (target/ours wins)
        assert entries_by_name["data.jsonl"].sidecar_hash == main_sc_hash
        # feat.jsonl should have feature's sidecar (source/theirs)
        assert entries_by_name["feat.jsonl"].sidecar_hash == feat_sc_hash
