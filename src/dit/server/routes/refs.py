import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref, Repo
from dit.server.webhooks import load_webhooks, fire_webhook_payloads, WebhookEvent

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["refs"])


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


@router.get("/refs/{ref_type}/{name}")
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


@router.post("/refs/{ref_type}/{name}")
async def cas_update_ref(
    repo: str,
    ref_type: str,
    name: str,
    body: CASRefRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"

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
        return {"name": ref_name, "target_hash": body.new}
    else:
        # CAS UPDATE
        result = await session.execute(
            select(Ref).where(
                Ref.repo_id == r.id,
                Ref.name == ref_name,
            )
        )
        ref = result.scalar_one_or_none()
        if ref is None:
            raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
        if ref.target_hash != body.old:
            raise HTTPException(
                status_code=409,
                detail=f"CAS conflict: expected {body.old[:8]}..., got {ref.target_hash[:8]}...",
            )
        ref.target_hash = body.new
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": body.old, "new_hash": body.new},
        ))
        return {"name": ref_name, "target_hash": body.new}
