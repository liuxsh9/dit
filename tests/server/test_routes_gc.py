"""Tests for GC API endpoint."""
import hashlib
import json
import os
import time

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.server.models import Ref, Repo, Token


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(store: ObjectStore, rows: list[dict], parent_hashes=None) -> str:
    from dit.core.hash import row_hash as compute_row_hash

    entries = []
    for row in rows:
        rh = compute_row_hash(row)
        _write_row(store, row)
        entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
    manifest = Manifest(entries=entries)
    m_hash = store.write("manifests", serialize_manifest(manifest))
    tree = Tree(entries=[TreeEntry(name="f.jsonl", obj_type="manifest", obj_hash=m_hash)])
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(tree_hash=t_hash, parent_hashes=parent_hashes or [], author="alice", message="test", timestamp=int(time.time()))
    return store.write("commits", serialize_commit(c))


@pytest.mark.asyncio
async def test_gc_dry_run(client: AsyncClient, session, tmp_path):
    """Dry-run GC should return live_counts with commits >= 1."""
    resp = await client.post("/api/v1/repos", json={"name": "gc-dry"})
    assert resp.status_code == 201
    repo_id = resp.json()["id"]

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "gc-dry" / "objects")
    c_hash = _make_commit(store, [{"text": "hello", "label": "pos"}])

    ref = Ref(repo_id=repo_id, name="refs/heads/main", target_hash=c_hash)
    session.add(ref)
    await session.commit()

    resp = await client.post(
        "/api/v1/repos/gc-dry/gc",
        json={"grace_hours": 24, "dry_run": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "live_counts" in body
    assert body["live_counts"].get("commits", 0) >= 1


@pytest.mark.asyncio
async def test_gc_actual_delete(client: AsyncClient, session, tmp_path):
    """GC should delete orphan objects older than grace period."""
    resp = await client.post("/api/v1/repos", json={"name": "gc-delete"})
    assert resp.status_code == 201
    repo_id = resp.json()["id"]

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "gc-delete" / "objects")
    c_hash = _make_commit(store, [{"text": "hello", "label": "pos"}])

    ref = Ref(repo_id=repo_id, name="refs/heads/main", target_hash=c_hash)
    session.add(ref)
    await session.commit()

    # Write an orphan row (not referenced by any commit)
    orphan_data = json.dumps({"orphan": True}, separators=(",", ":"), sort_keys=True).encode()
    orphan_hash = store.write("rows", orphan_data)

    # Backdate the orphan file's mtime to be older than grace period
    orphan_path = store.root / "rows" / orphan_hash[:2] / orphan_hash[2:4] / orphan_hash
    old_mtime = time.time() - 3 * 86400  # 3 days ago
    os.utime(orphan_path, (old_mtime, old_mtime))

    resp = await client.post(
        "/api/v1/repos/gc-delete/gc",
        json={"grace_hours": 24, "dry_run": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_deleted"] >= 1
    assert not store.exists("rows", orphan_hash)


@pytest.mark.asyncio
async def test_gc_requires_admin(client: AsyncClient, session, tmp_path):
    """GC endpoint should return 403 for non-admin tokens."""
    resp = await client.post("/api/v1/repos", json={"name": "gc-auth"})
    assert resp.status_code == 201

    # Create a reader token
    reader_token_raw = "test-reader-token-gc"
    token_hash = hashlib.sha256(reader_token_raw.encode()).hexdigest()
    reader_token = Token(
        token_hash=token_hash,
        label="test-reader-gc",
        permissions="read",
        role="reader",
    )
    session.add(reader_token)
    await session.commit()

    from httpx import ASGITransport, AsyncClient as RawClient
    transport = client._transport
    async with RawClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {reader_token_raw}"},
    ) as reader_client:
        resp = await reader_client.post(
            "/api/v1/repos/gc-auth/gc",
            json={"grace_hours": 24, "dry_run": True},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_gc_repo_not_found(client: AsyncClient, tmp_path):
    """GC on nonexistent repo should return 404."""
    resp = await client.post(
        "/api/v1/repos/nonexistent/gc",
        json={"dry_run": True},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_gc_default_values(client: AsyncClient, session, tmp_path):
    """GC with empty body should apply defaults and return 200."""
    resp = await client.post("/api/v1/repos", json={"name": "gc-defaults"})
    assert resp.status_code == 201
    repo_id = resp.json()["id"]

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "gc-defaults" / "objects")
    c_hash = _make_commit(store, [{"text": "defaults", "label": "test"}])

    ref = Ref(repo_id=repo_id, name="refs/heads/main", target_hash=c_hash)
    session.add(ref)
    await session.commit()

    resp = await client.post("/api/v1/repos/gc-defaults/gc", json={})
    assert resp.status_code == 200
    body = resp.json()
    # All objects are live (recently created), so nothing deleted
    assert body["total_deleted"] == 0
