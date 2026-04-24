import asyncio
import fnmatch

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import BranchProtection, Ref, Repo
from dit.server.webhooks import load_webhooks, fire_webhook_payloads, WebhookEvent

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["refs"])


async def _check_branch_protection(
    session: AsyncSession, repo_id: int, branch_name: str
) -> BranchProtection | None:
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == repo_id)
    )
    rules = result.scalars().all()
    for rule in rules:
        if fnmatch.fnmatch(branch_name, rule.branch_pattern):
            return rule
    return None


async def _update_prs_for_ref_change(
    session: AsyncSession,
    request: Request,
    repo_name: str,
    repo_id: int,
    ref_name: str,
    new_hash: str,
):
    """After a ref update, refresh any open PRs that reference this branch."""
    from dit.server.models import PullRequestMeta
    from dit.server.routes.pulls import _compute_diff_stats, _compute_mergeability, _store_for_repo

    store = _store_for_repo(request, repo_name)

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == repo_id,
            PullRequestMeta.status == "open",
            (
                (PullRequestMeta.source_ref == ref_name)
                | (PullRequestMeta.target_ref == ref_name)
            ),
        )
    )
    prs = result.scalars().all()

    for pr in prs:
        src_result = await session.execute(
            select(Ref).where(Ref.repo_id == repo_id, Ref.name == pr.source_ref)
        )
        src_ref = src_result.scalar_one_or_none()
        tgt_result = await session.execute(
            select(Ref).where(Ref.repo_id == repo_id, Ref.name == pr.target_ref)
        )
        tgt_ref = tgt_result.scalar_one_or_none()

        if src_ref is None or tgt_ref is None:
            continue

        source_commit = src_ref.target_hash
        target_commit = tgt_ref.target_hash

        pr.source_commit = source_commit
        pr.target_commit = target_commit

        from dit.core.merge_base import find_merge_base
        base_hash = find_merge_base(store, target_commit, source_commit)
        if base_hash is not None:
            pr.base_commit = base_hash

        diff_stats = _compute_diff_stats(store, source_commit, target_commit)
        pr.stats_added = diff_stats["stats_added"]
        pr.stats_removed = diff_stats["stats_removed"]
        pr.stats_refreshed = diff_stats["stats_refreshed"]

        mergeability = _compute_mergeability(store, target_commit, source_commit)
        pr.is_mergeable = mergeability["is_mergeable"]
        pr.conflict_files = mergeability.get("conflict_files")

    if prs:
        await session.commit()


class CASRefRequest(BaseModel):
    old: str | None = None
    new: str


async def _get_repo(repo: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo))
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Repo '{repo}' not found")
    return r


@router.get("/refs")
async def list_refs(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(select(Ref).where(Ref.repo_id == r.id).order_by(Ref.name))
    refs = result.scalars().all()
    return [{"name": ref.name, "target_hash": ref.target_hash} for ref in refs]


@router.get("/refs/{ref_type}/{name:path}")
async def get_ref(
    repo: str,
    ref_type: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
    return {"name": ref.name, "target_hash": ref.target_hash}


@router.post("/refs/{ref_type}/{name:path}")
async def cas_update_ref(
    repo: str,
    ref_type: str,
    name: str,
    body: CASRefRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"

    if body.old is not None and body.old != "" and ref_type == "heads":
        protection = await _check_branch_protection(session, r.id, name)
        if protection is not None and protection.require_pr:
            raise HTTPException(
                status_code=403,
                detail=f"Branch '{name}' is protected and requires a pull request. Direct push is not allowed.",
            )

    if body.old is None or body.old == "":
        # INSERT new ref
        existing = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Ref already exists")
        ref = Ref(repo_id=r.id, name=ref_name, target_hash=body.new)
        session.add(ref)
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": None, "new_hash": body.new},
        ))
        await _update_prs_for_ref_change(session, request, repo, r.id, ref_name, body.new)
        return {"name": ref_name, "target_hash": body.new}
    else:
        # Atomic CAS: single UPDATE with WHERE clause on current hash
        from sqlalchemy import update as sa_update
        stmt = (
            sa_update(Ref)
            .where(
                Ref.repo_id == r.id,
                Ref.name == ref_name,
                Ref.target_hash == body.old,
            )
            .values(target_hash=body.new)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            # Either ref doesn't exist, or target_hash didn't match
            check = await session.execute(
                select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
            )
            if check.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
            raise HTTPException(
                status_code=409,
                detail=f"CAS conflict: expected {body.old[:8]}...",
            )
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": body.old, "new_hash": body.new},
        ))
        await _update_prs_for_ref_change(session, request, repo, r.id, ref_name, body.new)
        return {"name": ref_name, "target_hash": body.new}


@router.delete("/refs/{ref_type}/{name:path}")
async def delete_ref(
    repo: str,
    ref_type: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
    await session.delete(ref)
    await session.commit()
    return {"status": "deleted"}
