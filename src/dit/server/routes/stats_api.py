# src/dit/server/routes/stats_api.py
"""Repo-level stats endpoint: GET /{repo}/stats/{commit_hash}"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["stats"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/stats/{commit_hash}")
async def repo_stats_endpoint(
    repo: str,
    commit_hash: str,
    request: Request,
    path: Optional[str] = Query(default=None, description="Filter to file/directory prefix"),
    include_size: bool = Query(default=True, description="Include exact row byte sizes"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return aggregated sidecar stats for all manifest files in a commit."""
    from dit.core.stats import repo_stats

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        result = await asyncio.to_thread(repo_stats, store, commit_hash, path_prefix=path, include_size=include_size)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result
