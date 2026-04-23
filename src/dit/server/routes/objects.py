import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.core.store import ObjectStore
from dit.server.auth import get_session, verify_token
from dit.server.models import Repo, Token

router = APIRouter(prefix="/api/v1/repos", tags=["objects"])


async def _get_repo(repo_name: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo_name))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


def _store_for_repo(request: Request, repo_name: str) -> ObjectStore:
    data_dir = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class BatchExistsIn(BaseModel):
    obj_type: str
    hashes: list[str]


class BatchExistsOut(BaseModel):
    exists: dict[str, bool]


@router.post("/{repo}/objects/batch-exists", response_model=BatchExistsOut)
async def batch_exists(
    repo: str,
    body: BatchExistsIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> BatchExistsOut:
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    result = store.batch_exists(body.obj_type, body.hashes)
    return BatchExistsOut(exists=result)


@router.get("/{repo}/objects/{obj_type}/{hash}", response_class=Response)
async def download_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> Response:
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    data = store.read(obj_type, hash)
    if data is None:
        raise HTTPException(status_code=404, detail="Object not found")
    return Response(content=data, media_type="application/octet-stream")


@router.post("/{repo}/objects/{obj_type}/{hash}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> None:
    await _get_repo(repo, session)
    body = await request.body()
    computed = hashlib.sha256(body).hexdigest()
    if computed != hash:
        raise HTTPException(
            status_code=400,
            detail=f"Hash mismatch: path has {hash}, body hashes to {computed}",
        )
    store = _store_for_repo(request, repo)
    store.write(obj_type, body)
