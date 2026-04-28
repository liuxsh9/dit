from __future__ import annotations

import asyncio
import fnmatch
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import BranchProtection, PrApproval, PullRequestMeta, Ref, Repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["merge"])


class MergePreviewRequest(BaseModel):
    source_branch: str
    target_branch: str


class MergeRequest(BaseModel):
    source_branch: str
    target_branch: str
    message: str
    author: str
    pull_request_id: int | None = None


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


async def _check_merge_approvals(
    session: AsyncSession, repo_id: int, target_branch: str, pull_request_id: int | None
) -> None:
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == repo_id)
    )
    rules = result.scalars().all()
    matched_rule = None
    for rule in rules:
        if fnmatch.fnmatch(target_branch, rule.branch_pattern):
            matched_rule = rule
            break

    if matched_rule is None:
        return

    # Block direct merge if require_pr is set
    if matched_rule.require_pr and pull_request_id is None:
        raise HTTPException(
            status_code=403,
            detail=f"Branch '{target_branch}' is protected and requires a pull request. Direct merge is not allowed.",
        )

    if matched_rule.required_approvals == 0:
        return

    if pull_request_id is None:
        raise HTTPException(
            status_code=403,
            detail=f"Branch '{target_branch}' requires {matched_rule.required_approvals} approval(s). Provide pull_request_id.",
        )

    pr_result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == repo_id,
            PullRequestMeta.pull_request_id == pull_request_id,
        )
    )
    if pr_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Pull request #{pull_request_id} not found")

    count_result = await session.execute(
        select(sa_func.count()).select_from(PrApproval).where(
            PrApproval.repo_id == repo_id,
            PrApproval.pull_request_id == pull_request_id,
            PrApproval.status == "approved",
        )
    )
    approval_count = count_result.scalar_one()
    if approval_count < matched_rule.required_approvals:
        raise HTTPException(
            status_code=403,
            detail=f"Branch '{target_branch}' requires {matched_rule.required_approvals} approval(s), but only {approval_count} found for PR {pull_request_id}.",
        )


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

    def _do_merge_preview():
        base = find_merge_base(store, target_hash, source_hash)
        result = three_way_merge(store, base, target_hash, source_hash)
        return base, result

    base_hash, merge_result = await asyncio.to_thread(_do_merge_preview)

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

    await _check_merge_approvals(session, r.id, body.target_branch, body.pull_request_id)

    base_hash = await asyncio.to_thread(find_merge_base, store, target_hash, source_hash)

    # Fast-forward check
    if base_hash == target_hash:
        target_ref_name = f"heads/{body.target_branch}"
        from sqlalchemy import update as sa_update

        stmt = (
            sa_update(Ref)
            .where(
                Ref.repo_id == r.id,
                Ref.name == target_ref_name,
                Ref.target_hash == target_hash,
            )
            .values(target_hash=source_hash)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            raise HTTPException(status_code=409, detail="Target branch was updated concurrently")
        await session.commit()
        return {"commit_hash": source_hash, "fast_forward": True}

    def _do_merge():
        from dit.core.tree_walker import flatten_tree
        from dit.core.objects import deserialize_commit

        mr = three_way_merge(store, base_hash, target_hash, source_hash)
        if mr.conflicts:
            return mr, None

        # Build sidecar_hash lookup: source (theirs) first, target (ours) wins
        target_commit_obj = deserialize_commit(store.read("commits", target_hash))
        source_commit_obj = deserialize_commit(store.read("commits", source_hash))
        target_flat = flatten_tree(store, target_commit_obj.tree_hash)
        source_flat = flatten_tree(store, source_commit_obj.tree_hash)
        sidecar_lookup: dict[str, str | None] = {}
        for path, (_t, _h, sc) in source_flat.items():
            if sc is not None:
                sidecar_lookup[path] = sc
        for path, (_t, _h, sc) in target_flat.items():
            if sc is not None:
                sidecar_lookup[path] = sc

        tree_entries = [
            TreeEntry(name=name, obj_type="manifest", obj_hash=mhash, sidecar_hash=sidecar_lookup.get(name))
            for name, mhash in mr.merged_tree_entries.items()
        ]
        tree = Tree(entries=tree_entries)
        tree_bytes = serialize_tree(tree)
        t_hash = store.write("trees", tree_bytes)
        commit = Commit(
            tree_hash=t_hash,
            parent_hashes=[target_hash, source_hash],
            author=body.author,
            message=body.message,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(commit)
        c_hash = store.write("commits", commit_bytes)
        return mr, c_hash

    merge_result, commit_hash = await asyncio.to_thread(_do_merge)

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
