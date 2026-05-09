from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from dit.server.auth import get_session, require_permission, resolve_actor
from dit.server.models import PrComment, PullRequestMeta
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["pr-comments"])

async def _get_pr_meta(session: AsyncSession, repo_id: int, pr_id: int) -> PullRequestMeta:
    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == repo_id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")
    return pr

def _serialize_comment(c: PrComment) -> dict:
    return {
        "id": c.id,
        "pull_request_meta_id": c.pull_request_meta_id,
        "author": c.author,
        "body": c.body,
        "file_path": c.file_path,
        "row_hash": c.row_hash,
        "field_path": c.field_path,
        "change_type": c.change_type,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }

class CreateCommentRequest(BaseModel):
    author: Optional[str] = None
    body: str
    file_path: Optional[str] = None
    row_hash: Optional[str] = None
    field_path: Optional[str] = None
    change_type: Optional[str] = None

class UpdateCommentRequest(BaseModel):
    body: Optional[str] = None

@router.post("/pulls/{pr_id}/comments", status_code=201)
async def create_comment(
    repo: str, pr_id: int, body: CreateCommentRequest,
    session: AsyncSession = Depends(get_session),
    token=Depends(require_permission("reviewer")),
):
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)
    comment = PrComment(
        pull_request_meta_id=pr_meta.id, author=resolve_actor(token, body.author), body=body.body,
        file_path=body.file_path, row_hash=body.row_hash,
        field_path=body.field_path, change_type=body.change_type,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return _serialize_comment(comment)

@router.get("/pulls/{pr_id}/comments")
async def list_comments(
    repo: str, pr_id: int,
    file_path: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)
    stmt = select(PrComment).where(PrComment.pull_request_meta_id == pr_meta.id).order_by(PrComment.created_at)
    if file_path:
        stmt = stmt.where(PrComment.file_path == file_path)
    result = await session.execute(stmt)
    return [_serialize_comment(c) for c in result.scalars().all()]

@router.patch("/pulls/{pr_id}/comments/{comment_id}")
async def update_comment(
    repo: str, pr_id: int, comment_id: int, body: UpdateCommentRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("reviewer")),
):
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)
    result = await session.execute(
        select(PrComment).where(PrComment.id == comment_id, PrComment.pull_request_meta_id == pr_meta.id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail=f"Comment #{comment_id} not found")
    if body.body is not None:
        comment.body = body.body
    await session.commit()
    await session.refresh(comment)
    return _serialize_comment(comment)

@router.delete("/pulls/{pr_id}/comments/{comment_id}")
async def delete_comment(
    repo: str, pr_id: int, comment_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)
    result = await session.execute(
        select(PrComment).where(PrComment.id == comment_id, PrComment.pull_request_meta_id == pr_meta.id)
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail=f"Comment #{comment_id} not found")
    await session.delete(comment)
    await session.commit()
    return {"status": "deleted", "id": comment_id}
