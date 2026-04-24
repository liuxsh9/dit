from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import BranchProtection
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["branch-protection"])


class CreateBranchProtectionRequest(BaseModel):
    branch_pattern: str
    require_pr: bool = True
    required_approvals: int = 1
    block_force_push: bool = True
    auto_delete_branch: bool = False


class UpdateBranchProtectionRequest(BaseModel):
    branch_pattern: Optional[str] = None
    require_pr: Optional[bool] = None
    required_approvals: Optional[int] = None
    block_force_push: Optional[bool] = None
    auto_delete_branch: Optional[bool] = None


def _serialize_rule(rule: BranchProtection) -> dict:
    return {
        "id": rule.id,
        "repo_id": rule.repo_id,
        "branch_pattern": rule.branch_pattern,
        "require_pr": rule.require_pr,
        "required_approvals": rule.required_approvals,
        "block_force_push": rule.block_force_push,
        "auto_delete_branch": rule.auto_delete_branch,
    }


@router.get("/branch-protection")
async def list_branch_protection_rules(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == r.id).order_by(BranchProtection.id)
    )
    return [_serialize_rule(rule) for rule in result.scalars().all()]


@router.post("/branch-protection", status_code=201)
async def create_branch_protection_rule(
    repo: str,
    body: CreateBranchProtectionRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    rule = BranchProtection(
        repo_id=r.id,
        branch_pattern=body.branch_pattern,
        require_pr=body.require_pr,
        required_approvals=body.required_approvals,
        block_force_push=body.block_force_push,
        auto_delete_branch=body.auto_delete_branch,
    )
    session.add(rule)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Branch protection rule for pattern '{body.branch_pattern}' already exists")
    await session.refresh(rule)
    return _serialize_rule(rule)


@router.put("/branch-protection/{rule_id}")
async def update_branch_protection_rule(
    repo: str,
    rule_id: int,
    body: UpdateBranchProtectionRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.id == rule_id, BranchProtection.repo_id == r.id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Branch protection rule #{rule_id} not found")
    if body.branch_pattern is not None:
        rule.branch_pattern = body.branch_pattern
    if body.require_pr is not None:
        rule.require_pr = body.require_pr
    if body.required_approvals is not None:
        rule.required_approvals = body.required_approvals
    if body.block_force_push is not None:
        rule.block_force_push = body.block_force_push
    if body.auto_delete_branch is not None:
        rule.auto_delete_branch = body.auto_delete_branch
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=409, detail=f"Branch protection rule for pattern '{body.branch_pattern}' already exists")
    await session.refresh(rule)
    return _serialize_rule(rule)


@router.delete("/branch-protection/{rule_id}")
async def delete_branch_protection_rule(
    repo: str,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.id == rule_id, BranchProtection.repo_id == r.id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Branch protection rule #{rule_id} not found")
    await session.delete(rule)
    await session.commit()
    return {"status": "deleted", "id": rule_id}
