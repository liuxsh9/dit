"""Tests for branch protection enforcement across merge, PR merge, and direct push."""
import time

import pytest

from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _setup_repo_with_branches(client, tmp_path, repo_name: str, diverged=True):
    """Create a repo with main and feature branches."""
    resp = await client.post("/api/v1/repos", json={"name": repo_name})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo_name / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
    row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")

    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(tree_hash=tree_base, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    h_base = store.write("commits", serialize_commit(c_base))

    if diverged:
        m_main = Manifest(entries=[row_a, row_b])
        m_main_hash = store.write("manifests", serialize_manifest(m_main))
        tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
        c_main = Commit(tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main commit", timestamp=int(time.time()))
        h_main = store.write("commits", serialize_commit(c_main))
    else:
        h_main = h_base

    m_feat = Manifest(entries=[row_a, row_c])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat commit", timestamp=int(time.time()))
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/main", json={"old": None, "new": h_main})
    await client.post(f"/api/v1/repos/{repo_name}/refs/heads/feature", json={"old": None, "new": h_feat})

    return store, h_base, h_main, h_feat


class TestDirectMergeRequirePR:
    """merge.py: require_pr=True should block direct merge without a PR."""

    async def test_direct_merge_blocked_when_require_pr(self, client, tmp_path):
        repo = "bp-require-pr-block"
        await _setup_repo_with_branches(client, tmp_path, repo)

        # Create protection rule with require_pr=True
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": True, "required_approvals": 0},
        )
        assert resp.status_code == 201

        # Direct merge without pull_request_id should be rejected
        resp = await client.post(
            f"/api/v1/repos/{repo}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Direct merge",
                "author": "tester",
            },
        )
        assert resp.status_code == 403
        assert "pull request" in resp.json()["detail"].lower()

    async def test_direct_merge_allowed_when_require_pr_false(self, client, tmp_path):
        repo = "bp-require-pr-allow"
        await _setup_repo_with_branches(client, tmp_path, repo)

        # Protection rule with require_pr=False
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": False, "required_approvals": 0},
        )
        assert resp.status_code == 201

        # Direct merge should succeed
        resp = await client.post(
            f"/api/v1/repos/{repo}/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Direct merge",
                "author": "tester",
            },
        )
        assert resp.status_code == 200


class TestPRMergeApprovalEnforcement:
    """pulls.py: required_approvals should be checked on PR merge."""

    async def test_pr_merge_blocked_insufficient_approvals(self, client, tmp_path):
        repo = "bp-pr-approvals-block"
        await _setup_repo_with_branches(client, tmp_path, repo)

        # Require 2 approvals
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": True, "required_approvals": 2},
        )
        assert resp.status_code == 201

        # Create PR
        pr_resp = await client.post(
            f"/api/v1/repos/{repo}/pulls",
            json={"title": "Feature PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["pull_request_id"]

        # Submit only 1 approval
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201

        # PR merge should be rejected (need 2, have 1)
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/merge",
            json={"message": "Merge feature", "author": "merger"},
        )
        assert resp.status_code == 403
        assert "approval" in resp.json()["detail"].lower()

    async def test_pr_merge_allowed_when_approvals_met(self, client, tmp_path):
        repo = "bp-pr-approvals-pass"
        await _setup_repo_with_branches(client, tmp_path, repo)

        # Require 1 approval
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": True, "required_approvals": 1},
        )
        assert resp.status_code == 201

        # Create PR
        pr_resp = await client.post(
            f"/api/v1/repos/{repo}/pulls",
            json={"title": "Feature PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["pull_request_id"]

        # Submit 1 approval
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201

        # PR merge should succeed
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/merge",
            json={"message": "Merge feature", "author": "merger"},
        )
        assert resp.status_code == 200

    async def test_pr_merge_no_protection_allows_merge(self, client, tmp_path):
        repo = "bp-pr-no-protection"
        await _setup_repo_with_branches(client, tmp_path, repo)

        # No protection rules — PR merge should work without approvals
        pr_resp = await client.post(
            f"/api/v1/repos/{repo}/pulls",
            json={"title": "Feature PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        assert pr_resp.status_code == 201
        pr_id = pr_resp.json()["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/merge",
            json={"message": "Merge feature", "author": "merger"},
        )
        assert resp.status_code == 200


class TestBlockForcePush:
    """refs.py: block_force_push=True should reject non-fast-forward ref updates."""

    async def test_force_push_blocked(self, client, tmp_path):
        repo = "bp-block-force-push"
        store, h_base, h_main, h_feat = await _setup_repo_with_branches(client, tmp_path, repo)

        # Create protection rule with block_force_push=True
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": False, "block_force_push": True},
        )
        assert resp.status_code == 201

        # Try to force-push main to h_feat (non-fast-forward: h_main and h_feat diverge from h_base)
        resp = await client.post(
            f"/api/v1/repos/{repo}/refs/heads/main",
            json={"old": h_main, "new": h_feat},
        )
        assert resp.status_code == 403
        assert "force push" in resp.json()["detail"].lower()

    async def test_fast_forward_push_allowed_with_block_force_push(self, client, tmp_path):
        repo = "bp-ff-push-allowed"
        store, h_base, h_main, h_feat = await _setup_repo_with_branches(client, tmp_path, repo, diverged=False)
        # h_main == h_base, h_feat is child of h_base => fast-forward is valid

        # Create protection rule with block_force_push=True but require_pr=False
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "require_pr": False, "block_force_push": True},
        )
        assert resp.status_code == 201

        # Fast-forward push (h_base -> h_feat where h_feat is descendant of h_base) should succeed
        resp = await client.post(
            f"/api/v1/repos/{repo}/refs/heads/main",
            json={"old": h_base, "new": h_feat},
        )
        assert resp.status_code == 200
