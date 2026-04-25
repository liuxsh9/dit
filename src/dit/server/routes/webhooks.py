from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Webhook
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["webhooks"])

_DEPRECATION_DATE = "Sat, 01 Jan 2027 00:00:00 GMT"
_SUNSET_DATE = "Sat, 01 Jul 2027 00:00:00 GMT"


def _add_deprecation_headers(response: Response) -> None:
    response.headers["Deprecation"] = _DEPRECATION_DATE
    response.headers["Sunset"] = _SUNSET_DATE
    response.headers["Link"] = '<https://forgejo.dit.example/docs/webhooks>; rel="successor-version"'


class CreateWebhookRequest(BaseModel):
    url: str
    secret: str = ""
    events: str


@router.post("/webhooks", status_code=201)
async def create_webhook(
    repo: str,
    body: CreateWebhookRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    wh = Webhook(repo_id=r.id, url=body.url, secret=body.secret, events=body.events)
    session.add(wh)
    await session.commit()
    await session.refresh(wh)
    return {"id": wh.id, "url": wh.url, "events": wh.events, "active": wh.active}


@router.get("/webhooks")
async def list_webhooks(
    repo: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    result = await session.execute(select(Webhook).where(Webhook.repo_id == r.id))
    hooks = result.scalars().all()
    return [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in hooks]


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    repo: str,
    webhook_id: int,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(Webhook).where(Webhook.repo_id == r.id, Webhook.id == webhook_id)
    )
    wh = result.scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await session.delete(wh)
    await session.commit()
    return {"status": "deleted"}
