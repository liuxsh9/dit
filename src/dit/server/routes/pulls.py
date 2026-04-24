from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import PullRequestMeta, Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["pulls"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreatePRRequest(BaseModel):
    title: str
    source_branch: str
    target_branch: str
    author: str
    description: Optional[str] = None


class UpdatePRRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


class MergePRRequest(BaseModel):
    message: str
    author: str


class ConflictResolutionRequest(BaseModel):
    resolutions: dict[str, str]  # file_path -> manifest_hash


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(data_dir / "repos" / repo_name / "objects")


async def _resolve_branch(session: AsyncSession, repo_id: int, branch: str) -> tuple[str, str]:
    """Return (ref_name, target_hash) for a branch."""
    ref_name = f"heads/{branch}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found")
    return ref_name, ref.target_hash


async def _next_pr_id(session: AsyncSession, repo_id: int) -> int:
    result = await session.execute(
        select(func.coalesce(func.max(PullRequestMeta.pull_request_id), 0)).where(
            PullRequestMeta.repo_id == repo_id
        )
    )
    return result.scalar_one() + 1


def _compute_diff_stats(store, source_commit: str, target_commit: str) -> dict:
    from dit.core.objects import deserialize_commit, deserialize_tree, deserialize_manifest
    from dit.core.tree_walker import flatten_tree
    from dit.core.diff import diff_manifests
    from dit.core.objects import Manifest

    def _get_commit_tree_hash(commit_hash: str) -> str:
        data = store.read("commits", commit_hash)
        commit = deserialize_commit(data)
        return commit.tree_hash

    source_tree_hash = _get_commit_tree_hash(source_commit)
    target_tree_hash = _get_commit_tree_hash(target_commit)

    source_flat = flatten_tree(store, source_tree_hash)
    target_flat = flatten_tree(store, target_tree_hash)

    all_paths = set(source_flat.keys()) | set(target_flat.keys())

    total_added = 0
    total_removed = 0
    total_refreshed = 0

    for path in all_paths:
        src_entry = source_flat.get(path)
        tgt_entry = target_flat.get(path)

        if src_entry is None:
            # File only in target (removed from source perspective)
            tgt_obj_type, tgt_hash = tgt_entry
            if tgt_obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", tgt_hash))
                total_removed += len(m.entries)
        elif tgt_entry is None:
            # File only in source (added)
            src_obj_type, src_hash = src_entry
            if src_obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", src_hash))
                total_added += len(m.entries)
        else:
            src_obj_type, src_hash = src_entry
            tgt_obj_type, tgt_hash = tgt_entry
            if src_obj_type == "manifest" and tgt_obj_type == "manifest":
                if src_hash != tgt_hash:
                    old_m = deserialize_manifest(store.read("manifests", tgt_hash))
                    new_m = deserialize_manifest(store.read("manifests", src_hash))
                    dr = diff_manifests(old_m, new_m)
                    total_added += len(dr.added)
                    total_removed += len(dr.removed)
                    total_refreshed += len(dr.refreshed)

    return {
        "stats_added": total_added,
        "stats_removed": total_removed,
        "stats_refreshed": total_refreshed,
    }


def _compute_mergeability(store, target_commit: str, source_commit: str) -> dict:
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge

    base_hash = find_merge_base(store, target_commit, source_commit)
    merge_result = three_way_merge(store, base_hash, target_commit, source_commit)

    is_mergeable = len(merge_result.conflicts) == 0
    conflict_files = [
        {"file_path": c.file_path, "conflict_type": c.conflict_type}
        for c in merge_result.conflicts
    ]

    return {
        "base_commit": base_hash,
        "is_mergeable": is_mergeable,
        "conflict_files": json.dumps(conflict_files),
    }


def _serialize_pr(pr: PullRequestMeta) -> dict:
    conflict_files = []
    if pr.conflict_files:
        try:
            conflict_files = json.loads(pr.conflict_files)
        except (json.JSONDecodeError, TypeError):
            conflict_files = []

    return {
        "id": pr.id,
        "pull_request_id": pr.pull_request_id,
        "repo_id": pr.repo_id,
        "title": pr.title,
        "author": pr.author,
        "status": pr.status,
        "source_ref": pr.source_ref,
        "target_ref": pr.target_ref,
        "base_commit": pr.base_commit,
        "source_commit": pr.source_commit,
        "target_commit": pr.target_commit,
        "merge_commit": pr.merge_commit,
        "is_mergeable": pr.is_mergeable,
        "conflict_files": conflict_files,
        "stats_added": pr.stats_added,
        "stats_removed": pr.stats_removed,
        "stats_refreshed": pr.stats_refreshed,
        "created_at": pr.created_at.isoformat() if pr.created_at else None,
        "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/pulls", status_code=201)
async def create_pull_request(
    repo: str,
    body: CreatePRRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    r = await _get_repo(repo, session)

    if body.source_branch == body.target_branch:
        raise HTTPException(status_code=400, detail="Source and target branches must be different")

    source_ref_name, source_commit = await _resolve_branch(session, r.id, body.source_branch)
    target_ref_name, target_commit = await _resolve_branch(session, r.id, body.target_branch)

    store = _store_for_repo(request, repo)

    # Compute diff stats
    diff_stats = _compute_diff_stats(store, source_commit, target_commit)

    # Compute mergeability
    mergeability = _compute_mergeability(store, target_commit, source_commit)

    pr_id = await _next_pr_id(session, r.id)

    pr = PullRequestMeta(
        repo_id=r.id,
        pull_request_id=pr_id,
        title=body.title,
        author=body.author,
        status="open",
        source_ref=source_ref_name,
        target_ref=target_ref_name,
        base_commit=mergeability["base_commit"] or "",
        source_commit=source_commit,
        target_commit=target_commit,
        merge_commit=None,
        is_mergeable=mergeability["is_mergeable"],
        conflict_files=mergeability["conflict_files"],
        stats_added=diff_stats["stats_added"],
        stats_removed=diff_stats["stats_removed"],
        stats_refreshed=diff_stats["stats_refreshed"],
    )
    session.add(pr)
    await session.commit()
    await session.refresh(pr)

    return _serialize_pr(pr)


@router.get("/pulls")
async def list_pull_requests(
    repo: str,
    status: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)

    stmt = select(PullRequestMeta).where(PullRequestMeta.repo_id == r.id)
    if status is not None:
        stmt = stmt.where(PullRequestMeta.status == status)
    stmt = stmt.order_by(PullRequestMeta.pull_request_id)

    result = await session.execute(stmt)
    prs = result.scalars().all()
    return [_serialize_pr(pr) for pr in prs]


@router.get("/pulls/{pr_id}")
async def get_pull_request(
    repo: str,
    pr_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"Pull request #{pr_id} not found")

    return _serialize_pr(pr)


@router.patch("/pulls/{pr_id}")
async def update_pull_request(
    repo: str,
    pr_id: int,
    body: UpdatePRRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    r = await _get_repo(repo, session)

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"Pull request #{pr_id} not found")

    if pr.status == "merged":
        raise HTTPException(status_code=400, detail="Cannot update a merged pull request")

    if body.title is not None:
        pr.title = body.title
    if body.status is not None:
        pr.status = body.status

    await session.commit()
    await session.refresh(pr)

    return _serialize_pr(pr)


@router.post("/pulls/{pr_id}/merge")
async def merge_pull_request(
    repo: str,
    pr_id: int,
    body: MergePRRequest,
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

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"Pull request #{pr_id} not found")

    if pr.status == "merged":
        raise HTTPException(status_code=400, detail="Pull request is already merged")
    if pr.status == "closed":
        raise HTTPException(status_code=400, detail="Cannot merge a closed pull request")

    store = _store_for_repo(request, repo)

    target_commit = pr.target_commit
    source_commit = pr.source_commit
    target_ref_name = pr.target_ref

    base_hash = find_merge_base(store, target_commit, source_commit)

    # Fast-forward check
    if base_hash == target_commit:
        stmt = (
            sa_update(Ref)
            .where(
                Ref.repo_id == r.id,
                Ref.name == target_ref_name,
                Ref.target_hash == target_commit,
            )
            .values(target_hash=source_commit)
            .execution_options(synchronize_session=False)
        )
        upd_result = await session.execute(stmt)
        if upd_result.rowcount == 0:
            raise HTTPException(status_code=409, detail="Target branch was updated concurrently")

        pr.status = "merged"
        pr.merge_commit = source_commit
        pr.target_commit = source_commit
        await session.commit()
        await session.refresh(pr)
        return _serialize_pr(pr)

    # Three-way merge
    merge_result = three_way_merge(store, base_hash, target_commit, source_commit)

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

    # Create merged tree
    tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    # Create merge commit
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[target_commit, source_commit],
        author=body.author,
        message=body.message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    commit_hash = store.write("commits", commit_bytes)

    # Atomic CAS update target branch
    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == target_ref_name,
            Ref.target_hash == target_commit,
        )
        .values(target_hash=commit_hash)
        .execution_options(synchronize_session=False)
    )
    upd_result = await session.execute(stmt)
    if upd_result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")

    pr.status = "merged"
    pr.merge_commit = commit_hash
    pr.target_commit = commit_hash
    await session.commit()
    await session.refresh(pr)

    return _serialize_pr(pr)
