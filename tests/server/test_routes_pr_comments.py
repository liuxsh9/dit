import time
import pytest
from pathlib import Path
from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree

async def _setup_comment_pr(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "comment-repo"})
    assert resp.status_code == 201
    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "comment-repo" / "objects")
    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint=None)
    m = Manifest(entries=[row_a])
    m_hash = store.write("manifests", serialize_manifest(m))
    tree_hash = build_nested_tree(store, {"data.jsonl": ("manifest", m_hash)})
    c = Commit(tree_hash=tree_hash, parent_hashes=[], author="test", message="init", timestamp=int(time.time()))
    h = store.write("commits", serialize_commit(c))
    await client.post("/api/v1/repos/comment-repo/refs/heads/main", json={"old": None, "new": h})
    await client.post("/api/v1/repos/comment-repo/refs/heads/feature", json={"old": None, "new": h})
    pr_resp = await client.post(
        "/api/v1/repos/comment-repo/pulls",
        json={"title": "Comment PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
    )
    assert pr_resp.status_code == 201
    return pr_resp.json()["pull_request_id"]

class TestCreateComment:
    async def test_create_general_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "reviewer", "body": "Looks good!"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["body"] == "Looks good!"
        assert data["author"] == "reviewer"
        assert data["file_path"] is None
        assert data["row_hash"] is None

    async def test_create_row_level_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={
                "author": "reviewer", "body": "This row needs work",
                "file_path": "data.jsonl", "row_hash": "a" * 64,
                "field_path": "messages[0].content", "change_type": "added",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["file_path"] == "data.jsonl"
        assert data["row_hash"] == "a" * 64
        assert data["field_path"] == "messages[0].content"
        assert data["change_type"] == "added"

    async def test_create_comment_pr_not_found(self, client, tmp_path):
        await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/comment-repo/pulls/999/comments",
            json={"author": "r", "body": "nope"},
        )
        assert resp.status_code == 404

class TestListComments:
    async def test_list_all_comments(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        await client.post(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments", json={"author": "r1", "body": "Comment 1"})
        await client.post(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments", json={"author": "r2", "body": "Comment 2"})
        resp = await client.get(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_list_comments_by_file(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        await client.post(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments", json={"author": "r1", "body": "On file", "file_path": "data.jsonl"})
        await client.post(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments", json={"author": "r2", "body": "General"})
        resp = await client.get(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments?file_path=data.jsonl")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file_path"] == "data.jsonl"

class TestUpdateComment:
    async def test_update_body(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        create_resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "Old body"},
        )
        comment_id = create_resp.json()["id"]
        resp = await client.patch(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments/{comment_id}",
            json={"body": "Updated body"},
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "Updated body"

class TestDeleteComment:
    async def test_delete_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        create_resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "To delete"},
        )
        comment_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments/{comment_id}")
        assert resp.status_code == 200
        list_resp = await client.get(f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments")
        assert len(list_resp.json()) == 0
