"""Blame API endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["blame"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/blame/{commit_hash}/{file_path:path}")
async def blame_endpoint(
    repo: str,
    commit_hash: str,
    file_path: str,
    row: Optional[int] = Query(default=None),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.blame import blame_file, row_history

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        if row is not None:
            try:
                return row_history(store, commit_hash, file_path, row)
            except IndexError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        else:
            return blame_file(store, commit_hash, file_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
