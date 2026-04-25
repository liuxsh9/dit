"""Sidecar metadata API endpoints."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["meta"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


def _sidecar_summary(sidecar) -> dict:
    row_count = len(sidecar.entries)
    if row_count == 0:
        return {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }
    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count
    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        k = e.lang or "unknown"
        lang_counts[k] = lang_counts.get(k, 0) + 1
    return {
        "row_count": row_count,
        "char_count": total_chars,
        "token_estimate": total_tokens,
        "avg_fields": round(avg_fields, 2),
        "lang_distribution": lang_counts,
    }


class MetaComputeRequest(BaseModel):
    file: Optional[str] = None


@router.post("/{repo}/meta/compute")
async def meta_compute(
    repo: str,
    body: MetaComputeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    from dit.core.objects import (
        deserialize_commit, serialize_commit, serialize_sidecar, Commit,
    )
    from dit.core.tree_walker import flatten_tree
    from dit.core.tree_builder import build_nested_tree
    from dit.core.sidecar import compute_sidecar

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == "heads/main")
    )
    ref_obj = result.scalar_one_or_none()
    if ref_obj is None:
        raise HTTPException(status_code=400, detail="Repository has no commits (no heads/main ref)")

    head_hash = ref_obj.target_hash
    commit_data = store.read("commits", head_hash)
    if commit_data is None:
        raise HTTPException(status_code=400, detail="HEAD commit not found in object store")

    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)

    computed: list[dict] = []
    updated: dict[str, tuple] = {}

    for path, (obj_type, obj_hash, sidecar_hash) in flat.items():
        if obj_type != "manifest":
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if body.file is not None and path != body.file.lstrip("/"):
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue
        if sidecar_hash is not None:
            updated[path] = (obj_type, obj_hash, sidecar_hash)
            continue

        sidecar = compute_sidecar(store, obj_hash)
        sidecar_bytes = serialize_sidecar(sidecar)
        new_sc_hash = store.write("sidecars", sidecar_bytes)
        updated[path] = (obj_type, obj_hash, new_sc_hash)
        computed.append({"file": path, "sidecar_hash": new_sc_hash})

    if not computed:
        return {"commit_hash": head_hash, "sidecars": []}

    new_tree_hash = build_nested_tree(store, updated)
    new_commit = Commit(
        tree_hash=new_tree_hash,
        parent_hashes=[head_hash],
        author="server",
        message="meta: compute sidecar metadata",
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(new_commit)
    new_commit_hash = store.write("commits", commit_bytes)

    from sqlalchemy import update as sa_update
    stmt = (
        sa_update(Ref)
        .where(Ref.repo_id == r.id, Ref.name == "heads/main", Ref.target_hash == head_hash)
        .values(target_hash=new_commit_hash)
        .execution_options(synchronize_session=False)
    )
    update_result = await session.execute(stmt)
    if update_result.rowcount == 0:
        raise HTTPException(status_code=409, detail="HEAD was updated concurrently — retry")
    await session.commit()

    return {"commit_hash": new_commit_hash, "sidecars": computed}


# IMPORTANT: diff route MUST be registered BEFORE the {commit_hash}/{file_path:path} routes
@router.get("/{repo}/meta/diff/{old_commit}/{new_commit}")
async def meta_diff(
    repo: str,
    old_commit: str,
    new_commit: str,
    request: Request,
    file: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    def _load_flat(commit_hash: str) -> dict:
        data = store.read("commits", commit_hash)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Commit {commit_hash[:8]} not found")
        commit = deserialize_commit(data)
        return flatten_tree(store, commit.tree_hash)

    old_flat = _load_flat(old_commit)
    new_flat = _load_flat(new_commit)

    all_paths = sorted(set(old_flat) | set(new_flat))
    if file:
        clean = file.lstrip("/")
        all_paths = [p for p in all_paths if p == clean]

    files = []
    for path in all_paths:
        old_entry = old_flat.get(path)
        new_entry = new_flat.get(path)

        if old_entry and old_entry[0] != "manifest":
            continue
        if new_entry and new_entry[0] != "manifest":
            continue

        def _get_summary(entry):
            if entry is None:
                return {"row_count": 0, "char_count": 0, "token_estimate": 0, "avg_fields": 0.0, "lang_distribution": {}}
            _, _, sc_hash = entry
            if sc_hash is None:
                return None
            sc_data = store.read("sidecars", sc_hash)
            if sc_data is None:
                return None
            return _sidecar_summary(deserialize_sidecar(sc_data))

        old_stats = _get_summary(old_entry)
        new_stats = _get_summary(new_entry)

        if old_stats is None or new_stats is None:
            continue
        if old_stats == new_stats:
            continue

        delta = {
            "row_count": new_stats["row_count"] - old_stats["row_count"],
            "char_count": new_stats["char_count"] - old_stats["char_count"],
            "token_estimate": new_stats["token_estimate"] - old_stats["token_estimate"],
        }

        files.append({
            "path": path,
            "old_stats": old_stats,
            "new_stats": new_stats,
            "delta": delta,
        })

    return {"old_commit": old_commit, "new_commit": new_commit, "files": files}


# summary MUST be registered BEFORE the bare file_path route
@router.get("/{repo}/meta/{commit_hash}/{file_path:path}/summary")
async def meta_summary(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean = file_path.lstrip("/")
    if clean not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean}' not found")

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        raise HTTPException(status_code=404, detail=f"'{clean}' is not a manifest")
    if sidecar_hash is None:
        raise HTTPException(status_code=404, detail=f"No sidecar for '{clean}'")

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        raise HTTPException(status_code=404, detail="Sidecar object missing from store")

    sidecar = deserialize_sidecar(sc_data)
    return _sidecar_summary(sidecar)


@router.get("/{repo}/meta/{commit_hash}/{file_path:path}")
async def meta_get(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.objects import deserialize_commit, deserialize_sidecar
    from dit.core.tree_walker import flatten_tree

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean = file_path.lstrip("/")
    if clean not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean}' not found")

    obj_type, obj_hash, sidecar_hash = flat[clean]
    if obj_type != "manifest":
        raise HTTPException(status_code=404, detail=f"'{clean}' is not a manifest")
    if sidecar_hash is None:
        raise HTTPException(status_code=404, detail=f"No sidecar computed for '{clean}'")

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        raise HTTPException(status_code=404, detail="Sidecar object missing from store")

    sidecar = deserialize_sidecar(sc_data)
    return {
        "commit_hash": commit_hash,
        "path": clean,
        "manifest_hash": sidecar.manifest_hash,
        "entries": [
            {
                "row_hash": e.row_hash,
                "char_count": e.char_count,
                "token_estimate": e.token_estimate,
                "field_count": e.field_count,
                "lang": e.lang,
            }
            for e in sidecar.entries
        ],
    }
