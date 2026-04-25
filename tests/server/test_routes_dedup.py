"""Tests for dedup API endpoint."""
import json
import time

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.server.models import Repo


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg, asst_msg):
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store, files, parent_hashes=None):
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(tree_hash=t_hash, parent_hashes=parent_hashes or [], author="alice", message="test", timestamp=int(time.time()))
    return store.write("commits", serialize_commit(c))


@pytest.mark.asyncio
async def test_dedup_clean(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-clean")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-clean" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})

    resp = await client.get(f"/api/v1/repos/dedup-clean/dedup/{c}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["severity"] == "clean"


@pytest.mark.asyncio
async def test_dedup_exact_duplicates(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-exact")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-exact" / "objects")
    row = _conv("q1", "a1")
    c = _make_commit(store, {"f.jsonl": [row, row]})

    resp = await client.get(f"/api/v1/repos/dedup-exact/dedup/{c}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["severity"] == "warning"
    assert data["summary"]["exact_dup_groups"] == 1


@pytest.mark.asyncio
async def test_dedup_with_path_filter(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-path")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-path" / "objects")
    row = _conv("q1", "a1")
    c = _make_commit(store, {"train.jsonl": [row], "eval.jsonl": [row]})

    resp = await client.get(f"/api/v1/repos/dedup-path/dedup/{c}?path=train")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_files"] == 1
    assert data["summary"]["exact_dup_groups"] == 0


@pytest.mark.asyncio
async def test_dedup_commit_not_found(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-404")
    session.add(repo)
    await session.commit()

    resp = await client.get(f"/api/v1/repos/dedup-404/dedup/{'0'*64}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dedup_repo_not_found(client: AsyncClient):
    resp = await client.get(f"/api/v1/repos/nonexistent/dedup/{'0'*64}")
    assert resp.status_code == 404
