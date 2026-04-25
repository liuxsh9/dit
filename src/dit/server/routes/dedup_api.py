"""Dedup API endpoint."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["dedup"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/dedup/{commit_hash}")
async def dedup_endpoint(
    repo: str,
    commit_hash: str,
    path: Optional[str] = Query(default=None),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.dedup import detect_duplicates

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        return detect_duplicates(store, commit_hash, path_prefix=path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
