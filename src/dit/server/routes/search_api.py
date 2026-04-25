# src/dit/server/routes/search_api.py
"""Row-level search endpoint: POST /{repo}/search"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["search"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class SearchRequest(BaseModel):
    ref: str = "heads/main"
    query: str
    file: str | None = None
    field: str | None = None
    limit: int = 50


@router.post("/{repo}/search")
async def repo_search_endpoint(
    repo: str,
    body: SearchRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Brute-force substring search across rows in a commit."""
    from dit.core.search import search_rows

    r = await _get_repo(repo, session)

    # Resolve ref to commit hash
    # If body.ref looks like a full hex hash (64 chars), use it directly
    if len(body.ref) == 64 and all(c in "0123456789abcdef" for c in body.ref):
        commit_hash = body.ref
    else:
        # Treat body.ref as a ref name (e.g. "heads/main")
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.ref)
        )
        ref_obj = result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.ref}' not found")
        commit_hash = ref_obj.target_hash

    store = _store_for_repo(request, repo)

    try:
        result = search_rows(
            store,
            commit_hash,
            body.query,
            path_prefix=body.file,
            field_path=body.field,
            limit=body.limit,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result
