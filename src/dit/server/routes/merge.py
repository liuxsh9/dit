from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref, Repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["merge"])


class MergePreviewRequest(BaseModel):
    source_branch: str
    target_branch: str


class MergeRequest(BaseModel):
    source_branch: str
    target_branch: str
    message: str
    author: str


async def _get_repo(repo: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo))
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Repo '{repo}' not found")
    return r


def _get_store(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(data_dir / "repos" / repo_name / "objects")


async def _resolve_branch(session: AsyncSession, repo_id: int, branch: str) -> str:
    ref_name = f"heads/{branch}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found")
    return ref.target_hash


@router.post("/merge-preview")
async def merge_preview(
    repo: str,
    body: MergePreviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    store = _get_store(request, repo)

    source_hash = await _resolve_branch(session, r.id, body.source_branch)
    target_hash = await _resolve_branch(session, r.id, body.target_branch)

    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge

    base_hash = find_merge_base(store, target_hash, source_hash)
    merge_result = three_way_merge(store, base_hash, target_hash, source_hash)

    return {
        "mergeable": len(merge_result.conflicts) == 0,
        "merge_base": base_hash,
        "conflicts": [
            {"file_path": c.file_path, "conflict_type": c.conflict_type}
            for c in merge_result.conflicts
        ],
    }


@router.post("/merge")
async def merge(
    repo: str,
    body: MergeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge
    from dit.core.objects import (
        Commit,
        Tree,
        TreeEntry,
        serialize_commit,
        serialize_tree,
    )

    r = await _get_repo(repo, session)
    store = _get_store(request, repo)

    source_hash = await _resolve_branch(session, r.id, body.source_branch)
    target_hash = await _resolve_branch(session, r.id, body.target_branch)

    base_hash = find_merge_base(store, target_hash, source_hash)

    # Fast-forward check
    if base_hash == target_hash:
        target_ref_name = f"heads/{body.target_branch}"
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == target_ref_name)
        )
        ref = result.scalar_one_or_none()
        if ref is None:
            raise HTTPException(status_code=404, detail="Target branch ref not found")
        ref.target_hash = source_hash
        await session.commit()
        return {"commit_hash": source_hash, "fast_forward": True}

    merge_result = three_way_merge(store, base_hash, target_hash, source_hash)

    if merge_result.conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Merge conflicts — cannot auto-merge",
                "conflicts": [
                    {"file_path": c.file_path, "conflict_type": c.conflict_type}
                    for c in merge_result.conflicts
                ],
            },
        )

    # Create merge commit
    tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[target_hash, source_hash],
        author=body.author,
        message=body.message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    commit_hash = store.write("commits", commit_bytes)

    # CAS update target branch: SELECT then UPDATE pattern for SQLite compatibility
    target_ref_name = f"heads/{body.target_branch}"
    from sqlalchemy import update as sa_update

    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == target_ref_name,
            Ref.target_hash == target_hash,
        )
        .values(target_hash=commit_hash)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")
    await session.commit()

    return {"commit_hash": commit_hash, "fast_forward": False}
