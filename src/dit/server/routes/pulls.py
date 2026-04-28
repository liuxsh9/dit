from __future__ import annotations

import asyncio
import fnmatch
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
from dit.server.models import BranchProtection, PrApproval, PullRequestMeta, Ref
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


class ConflictResolution(BaseModel):
    file_path: str
    row_hash: str
    choice: str  # "ours" | "theirs"


class ConflictResolutionRequest(BaseModel):
    resolutions: list[ConflictResolution]
    message: str
    author: str


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
            tgt_obj_type, tgt_hash, _sc = tgt_entry
            if tgt_obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", tgt_hash))
                total_removed += len(m.entries)
        elif tgt_entry is None:
            # File only in source (added)
            src_obj_type, src_hash, _sc = src_entry
            if src_obj_type == "manifest":
                m = deserialize_manifest(store.read("manifests", src_hash))
                total_added += len(m.entries)
        else:
            src_obj_type, src_hash, _sc1 = src_entry
            tgt_obj_type, tgt_hash, _sc2 = tgt_entry
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


async def _check_pr_merge_approvals(
    session: AsyncSession, repo_id: int, target_ref: str, pull_request_id: int
) -> None:
    """Check branch protection approval requirements for a PR merge."""
    # target_ref is like "heads/main", extract branch name
    branch_name = target_ref.removeprefix("heads/")

    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == repo_id)
    )
    rules = result.scalars().all()
    matched_rule = None
    for rule in rules:
        if fnmatch.fnmatch(branch_name, rule.branch_pattern):
            matched_rule = rule
            break

    if matched_rule is None or matched_rule.required_approvals == 0:
        return

    count_result = await session.execute(
        select(func.count()).select_from(PrApproval).where(
            PrApproval.repo_id == repo_id,
            PrApproval.pull_request_id == pull_request_id,
            PrApproval.status == "approved",
        )
    )
    approval_count = count_result.scalar_one()
    if approval_count < matched_rule.required_approvals:
        raise HTTPException(
            status_code=403,
            detail=f"Branch '{branch_name}' requires {matched_rule.required_approvals} approval(s), but only {approval_count} found for PR #{pull_request_id}.",
        )


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

    # Compute diff stats and mergeability off the event loop
    diff_stats, mergeability = await asyncio.to_thread(
        lambda: (
            _compute_diff_stats(store, source_commit, target_commit),
            _compute_mergeability(store, target_commit, source_commit),
        )
    )

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

    # Check branch protection approval requirements
    await _check_pr_merge_approvals(session, r.id, pr.target_ref, pr.pull_request_id)

    store = _store_for_repo(request, repo)

    target_commit = pr.target_commit
    source_commit = pr.source_commit
    target_ref_name = pr.target_ref

    base_hash = await asyncio.to_thread(find_merge_base, store, target_commit, source_commit)

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
        result_dict = _serialize_pr(pr)
        result_dict["fast_forward"] = True
        return result_dict

    # Three-way merge + commit creation off the event loop
    def _do_merge():
        from dit.core.tree_walker import flatten_tree
        from dit.core.objects import deserialize_commit

        mr = three_way_merge(store, base_hash, target_commit, source_commit)
        if mr.conflicts:
            return mr, None

        # Build sidecar_hash lookup: source (theirs) first, target (ours) wins
        target_commit_obj = deserialize_commit(store.read("commits", target_commit))
        source_commit_obj = deserialize_commit(store.read("commits", source_commit))
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
            parent_hashes=[target_commit, source_commit],
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

    result_dict = _serialize_pr(pr)
    result_dict["fast_forward"] = False
    return result_dict


@router.post("/pulls/{pr_id}/resolve")
async def resolve_conflicts(
    repo: str,
    pr_id: int,
    body: ConflictResolutionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge
    from dit.core.objects import (
        Commit,
        Manifest,
        ManifestEntry,
        Tree,
        TreeEntry,
        deserialize_manifest,
        serialize_commit,
        serialize_manifest,
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

    if pr.status != "open":
        raise HTTPException(status_code=400, detail=f"Pull request is not open (status: {pr.status})")

    store = _store_for_repo(request, repo)

    # Re-resolve current branch heads
    target_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.target_ref)
    )
    target_ref = target_ref_result.scalar_one_or_none()
    if target_ref is None:
        raise HTTPException(status_code=404, detail=f"Target branch not found")

    source_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.source_ref)
    )
    source_ref = source_ref_result.scalar_one_or_none()
    if source_ref is None:
        raise HTTPException(status_code=404, detail=f"Source branch not found")

    target_commit = target_ref.target_hash
    source_commit = source_ref.target_hash

    # Run merge base + three-way merge off the event loop
    def _do_merge_for_resolve():
        base = find_merge_base(store, target_commit, source_commit)
        mr = three_way_merge(store, base, target_commit, source_commit)
        return base, mr

    base_hash, merge_result = await asyncio.to_thread(_do_merge_for_resolve)

    if not merge_result.conflicts:
        raise HTTPException(status_code=400, detail="No conflicts to resolve — use /merge instead")

    # Build resolution map: file_path -> chosen row_hash
    resolution_map: dict[str, str] = {}
    for res in body.resolutions:
        resolution_map[res.file_path] = res.row_hash

    # Validate resolutions (needs to raise HTTPException, so stays in async handler)
    # Start with the auto-merged entries
    merged_tree_entries = dict(merge_result.merged_tree_entries)

    resolved_entries_map: dict[str, ManifestEntry] = {}
    for conflict in merge_result.conflicts:
        fp = conflict.file_path
        chosen_hash = resolution_map.get(fp)
        if chosen_hash is None:
            raise HTTPException(
                status_code=422,
                detail=f"No resolution provided for conflict in '{fp}'",
            )

        all_candidates: list[ManifestEntry] = []
        if conflict.ours_entries:
            all_candidates.extend(conflict.ours_entries)
        if conflict.theirs_entries:
            all_candidates.extend(conflict.theirs_entries)

        chosen_entry = next((e for e in all_candidates if e.row_hash == chosen_hash), None)
        if chosen_entry is None:
            raise HTTPException(
                status_code=422,
                detail=f"Row hash '{chosen_hash}' not found in conflict entries for '{fp}'",
            )
        resolved_entries_map[fp] = chosen_entry

    # Build resolved tree + commit off the event loop
    def _do_resolve_commit():
        from dit.core.tree_walker import flatten_tree
        from dit.core.objects import deserialize_commit as _deser_commit

        for fp, chosen_entry in resolved_entries_map.items():
            resolved_manifest = Manifest(entries=[chosen_entry])
            resolved_bytes = serialize_manifest(resolved_manifest)
            resolved_hash = store.write("manifests", resolved_bytes)
            merged_tree_entries[fp] = resolved_hash

        # Build sidecar_hash lookup: source (theirs) first, target (ours) wins
        target_commit_obj = _deser_commit(store.read("commits", target_commit))
        source_commit_obj = _deser_commit(store.read("commits", source_commit))
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
            for name, mhash in merged_tree_entries.items()
        ]
        tree = Tree(entries=tree_entries)
        tree_bytes = serialize_tree(tree)
        t_hash = store.write("trees", tree_bytes)

        commit = Commit(
            tree_hash=t_hash,
            parent_hashes=[target_commit, source_commit],
            author=body.author,
            message=body.message,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(commit)
        return store.write("commits", commit_bytes)

    commit_hash = await asyncio.to_thread(_do_resolve_commit)

    # Atomic CAS update target branch
    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == pr.target_ref,
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

