import json
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree
from dit.core.hash import canonical_json, row_hash


def _make_row(store, content: str) -> ManifestEntry:
    row = {"messages": [{"role": "user", "content": content}]}
    canon = canonical_json(row)
    rh = row_hash(row)
    store.write("rows", canon)
    return ManifestEntry(row_hash=rh, query_fingerprint=None)


def _make_refreshable_row(store, user_content: str, assistant_content: str) -> ManifestEntry:
    row = {"messages": [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": assistant_content},
    ]}
    canon = canonical_json(row)
    rh = row_hash(row)
    store.write("rows", canon)
    from dit.core.hash import query_fingerprint
    qfp = query_fingerprint(row)
    return ManifestEntry(row_hash=rh, query_fingerprint=qfp)


async def _setup_enhanced_diff_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "ediff-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "ediff-repo" / "objects")

    row_a = _make_row(store, "hello")
    row_b = _make_row(store, "world")
    row_c = _make_row(store, "new row")
    row_d = _make_row(store, "extra in file2")
    old_refresh = _make_refreshable_row(store, "sort algo", "old answer")
    new_refresh = _make_refreshable_row(store, "sort algo", "new better answer")

    m1_old = Manifest(entries=[row_a, row_b, old_refresh])
    m1_old_hash = store.write("manifests", serialize_manifest(m1_old))

    m1_new = Manifest(entries=[row_b, row_c, new_refresh])
    m1_new_hash = store.write("manifests", serialize_manifest(m1_new))

    m2_old = Manifest(entries=[row_a])
    m2_old_hash = store.write("manifests", serialize_manifest(m2_old))

    m2_new = Manifest(entries=[row_a, row_d])
    m2_new_hash = store.write("manifests", serialize_manifest(m2_new))

    staged_old = {
        "train/data.jsonl": ("manifest", m1_old_hash),
        "eval/test.jsonl": ("manifest", m2_old_hash),
    }
    staged_new = {
        "train/data.jsonl": ("manifest", m1_new_hash),
        "eval/test.jsonl": ("manifest", m2_new_hash),
    }

    tree_old = build_nested_tree(store, staged_old)
    tree_new = build_nested_tree(store, staged_new)

    c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
    h_old = store.write("commits", serialize_commit(c_old))

    c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
    h_new = store.write("commits", serialize_commit(c_new))

    await client.post(
        "/api/v1/repos/ediff-repo/refs/heads/main",
        json={"old": None, "new": h_old},
    )
    await client.post(
        "/api/v1/repos/ediff-repo/refs/heads/feature",
        json={"old": None, "new": h_new},
    )

    return store, h_old, h_new, row_a, row_b, row_c, row_d, old_refresh, new_refresh


class TestDiffSummaryOnly:
    async def test_summary_without_rows(self, client, tmp_path):
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 2
        for f in data["files"]:
            assert "path" in f
            assert "added" in f
            assert "removed" in f
            assert "refreshed" in f
            assert "added_rows" not in f
            assert "removed_rows" not in f

    async def test_summary_has_global_totals(self, client, tmp_path):
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        data = resp.json()
        assert "summary" in data
        assert data["summary"]["files_changed"] == 2
        assert data["summary"]["rows_added"] >= 1
        assert data["summary"]["rows_removed"] >= 1


class TestDiffPerFileDetail:
    async def test_single_file_filter(self, client, tmp_path):
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "train/data.jsonl"
        assert "added_rows" in f
        assert "removed_rows" in f
        assert "refreshed_rows" in f

    async def test_row_content_included(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        added_hashes = {r["row_hash"] for r in f["added_rows"]}
        removed_hashes = {r["row_hash"] for r in f["removed_rows"]}
        assert row_c.row_hash in added_hashes
        assert row_a.row_hash in removed_hashes
        for r in f["added_rows"]:
            if r["row_hash"] == row_c.row_hash:
                assert r["content"] is not None
                assert "messages" in r["content"]

    async def test_position_field_present(self, client, tmp_path):
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        for r in f["added_rows"] + f["removed_rows"]:
            assert "position" in r
            assert isinstance(r["position"], int)

    async def test_refreshed_rows_have_old_new(self, client, tmp_path):
        store, h_old, h_new, *_, old_refresh, new_refresh = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        assert len(f["refreshed_rows"]) >= 1
        ref = f["refreshed_rows"][0]
        assert "old_row_hash" in ref
        assert "new_row_hash" in ref
        assert "query_fingerprint" in ref
        assert ref["old_content"]["messages"][1]["content"] == "old answer"
        assert ref["new_content"]["messages"][1]["content"] == "new better answer"


class TestDiffPagination:
    async def test_pagination_offset_limit(self, client, tmp_path):
        resp = await client.post("/api/v1/repos", json={"name": "ediff-page-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "ediff-page-repo" / "objects")

        rows = [_make_row(store, f"row {i}") for i in range(20)]
        m_old = Manifest(entries=[])
        m_new = Manifest(entries=rows)
        m_old_hash = store.write("manifests", serialize_manifest(m_old))
        m_new_hash = store.write("manifests", serialize_manifest(m_new))

        tree_old = build_nested_tree(store, {"data.jsonl": ("manifest", m_old_hash)})
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})

        c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
        h_old = store.write("commits", serialize_commit(c_old))
        c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
        h_new = store.write("commits", serialize_commit(c_new))

        resp = await client.post(
            "/api/v1/repos/ediff-page-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "data.jsonl",
                "include_rows": True,
                "offset": 5,
                "limit": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        f = data["files"][0]
        assert len(f["added_rows"]) == 3
        assert f["added_rows"][0]["position"] == 5

    async def test_has_more_field(self, client, tmp_path):
        resp = await client.post("/api/v1/repos", json={"name": "ediff-more-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "ediff-more-repo" / "objects")

        rows = [_make_row(store, f"row {i}") for i in range(10)]
        m_old = Manifest(entries=[])
        m_new = Manifest(entries=rows)
        m_old_hash = store.write("manifests", serialize_manifest(m_old))
        m_new_hash = store.write("manifests", serialize_manifest(m_new))

        tree_old = build_nested_tree(store, {"data.jsonl": ("manifest", m_old_hash)})
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})

        c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
        h_old = store.write("commits", serialize_commit(c_old))
        c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
        h_new = store.write("commits", serialize_commit(c_new))

        resp = await client.post(
            "/api/v1/repos/ediff-more-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "data.jsonl",
                "include_rows": True,
                "offset": 0,
                "limit": 5,
            },
        )
        data = resp.json()
        f = data["files"][0]
        assert f["has_more"] is True
        assert f["total_changes"] == 10


class TestDiffRefBased:
    async def test_diff_by_ref_names(self, client, tmp_path):
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"from_ref": "heads/main", "to_ref": "heads/feature"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 2
