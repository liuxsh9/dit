import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Repo

router = APIRouter(prefix="/api/v1/repos", tags=["repos"])

_REPO_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


class CreateRepoRequest(BaseModel):
    name: str


class RepoResponse(BaseModel):
    id: int
    name: str
    created_at: str

    model_config = {"from_attributes": True}


@router.post("", status_code=201)
async def create_repo(
    body: CreateRepoRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    if not body.name or len(body.name) > 128 or not _REPO_NAME_RE.match(body.name):
        raise HTTPException(
            status_code=400,
            detail="Invalid repo name: must be 1-128 characters, only [a-zA-Z0-9._-] allowed",
        )
    existing = await session.execute(select(Repo).where(Repo.name == body.name))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Repo '{body.name}' already exists")
    repo = Repo(name=body.name)
    session.add(repo)
    await session.commit()
    await session.refresh(repo)
    return {"id": repo.id, "name": repo.name}


@router.get("")
async def list_repos(
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    result = await session.execute(select(Repo).order_by(Repo.name))
    repos = result.scalars().all()
    return [{"id": r.id, "name": r.name} for r in repos]
