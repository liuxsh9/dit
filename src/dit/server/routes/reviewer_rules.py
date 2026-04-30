from __future__ import annotations

import fnmatch

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import ReviewerRule
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["reviewer-rules"])


class CreateReviewerRuleRequest(BaseModel):
    pattern: str
    reviewer_token_id: int | None = None


class MatchReviewerRulesRequest(BaseModel):
    file_paths: list[str]


def _serialize_rule(rule: ReviewerRule) -> dict:
    return {
        "id": rule.id,
        "repo_id": rule.repo_id,
        "pattern": rule.pattern,
        "reviewer_token_id": rule.reviewer_token_id,
    }


@router.get("/reviewer-rules")
async def list_reviewer_rules(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule).where(ReviewerRule.repo_id == r.id).order_by(ReviewerRule.id)
    )
    return [_serialize_rule(rule) for rule in result.scalars().all()]


@router.post("/reviewer-rules", status_code=201)
async def create_reviewer_rule(
    repo: str,
    body: CreateReviewerRuleRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    rule = ReviewerRule(
        repo_id=r.id,
        pattern=body.pattern,
        reviewer_token_id=body.reviewer_token_id,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _serialize_rule(rule)


@router.delete("/reviewer-rules/{rule_id}")
async def delete_reviewer_rule(
    repo: str,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule).where(ReviewerRule.id == rule_id, ReviewerRule.repo_id == r.id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Reviewer rule #{rule_id} not found")
    await session.delete(rule)
    await session.commit()
    return {"status": "deleted", "id": rule_id}


@router.post("/reviewer-rules/match")
async def match_reviewer_rules(
    repo: str,
    body: MatchReviewerRulesRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule).where(ReviewerRule.repo_id == r.id).order_by(ReviewerRule.id)
    )
    rules = result.scalars().all()
    matched = [
        _serialize_rule(rule)
        for rule in rules
        if any(fnmatch.fnmatch(path, rule.pattern) for path in body.file_paths)
    ]
    return matched
