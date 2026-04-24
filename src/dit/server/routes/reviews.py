from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import PrApproval
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["reviews"])

ReviewStatus = Literal["approved", "changes_requested"]

class SubmitReviewRequest(BaseModel):
    status: ReviewStatus

def _approval_to_dict(a: PrApproval) -> dict:
    return {
        "id": a.id,
        "pull_request_id": a.pull_request_id,
        "token_id": a.token_id,
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }

@router.post("/pulls/{pull_request_id}/reviews", status_code=201)
async def submit_review(
    repo: str,
    pull_request_id: int,
    body: SubmitReviewRequest,
    session: AsyncSession = Depends(get_session),
    token=Depends(require_permission("reviewer")),
):
    await _get_repo(repo, session)
    # Upsert: if same token already reviewed this PR, update status
    existing = await session.execute(
        select(PrApproval).where(
            PrApproval.pull_request_id == pull_request_id,
            PrApproval.token_id == token.id,
        )
    )
    approval = existing.scalar_one_or_none()
    if approval is not None:
        approval.status = body.status
        await session.commit()
        await session.refresh(approval)
    else:
        approval = PrApproval(
            pull_request_id=pull_request_id,
            token_id=token.id,
            status=body.status,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
    return _approval_to_dict(approval)

@router.get("/pulls/{pull_request_id}/reviews")
async def list_reviews(
    repo: str,
    pull_request_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    await _get_repo(repo, session)
    result = await session.execute(
        select(PrApproval)
        .where(PrApproval.pull_request_id == pull_request_id)
        .order_by(PrApproval.created_at)
    )
    return [_approval_to_dict(a) for a in result.scalars().all()]
