"""Tests for fsck API endpoint."""
import hashlib
import json
import time

import pyzstd
import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.server.models import Ref, Repo, Token


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
async def test_fsck_clean(client: AsyncClient, session, tmp_path):
    repo = Repo(name="fsck-clean")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-clean" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    resp = await client.post("/api/v1/repos/fsck-clean/fsck", json={"check_hashes": True, "check_graph": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_errors"] == 0


@pytest.mark.asyncio
async def test_fsck_with_corruption(client: AsyncClient, session, tmp_path):
    from dit.core.hash import row_hash as compute_row_hash

    repo = Repo(name="fsck-corrupt")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-corrupt" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    row_hash = compute_row_hash(_conv("q1", "a1"))
    obj_path = store._object_path("rows", row_hash)
    obj_path.write_bytes(pyzstd.compress(b"corrupted"))

    resp = await client.post("/api/v1/repos/fsck-corrupt/fsck", json={"check_hashes": True, "check_graph": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_errors"] >= 1


@pytest.mark.asyncio
async def test_fsck_repo_not_found(client: AsyncClient):
    resp = await client.post("/api/v1/repos/nonexistent/fsck", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fsck_defaults(client: AsyncClient, session, tmp_path):
    repo = Repo(name="fsck-defaults")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-defaults" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    resp = await client.post("/api/v1/repos/fsck-defaults/fsck", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "checked_objects" in data
    assert "total_checked" in data


@pytest.mark.asyncio
async def test_fsck_requires_admin(client: AsyncClient, session, tmp_path):
    """fsck endpoint should return 403 for non-admin tokens."""
    resp = await client.post("/api/v1/repos", json={"name": "fsck-auth"})
    assert resp.status_code == 201

    # Create a reader token
    reader_token_raw = "test-reader-token-fsck"
    token_hash = hashlib.sha256(reader_token_raw.encode()).hexdigest()
    reader_token = Token(
        token_hash=token_hash,
        label="test-reader-fsck",
        permissions="read",
        role="reader",
    )
    session.add(reader_token)
    await session.commit()

    from httpx import AsyncClient as RawClient
    transport = client._transport
    async with RawClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {reader_token_raw}"},
    ) as reader_client:
        resp = await reader_client.post(
            "/api/v1/repos/fsck-auth/fsck",
            json={"check_hashes": True, "check_graph": True},
        )
    assert resp.status_code == 403
