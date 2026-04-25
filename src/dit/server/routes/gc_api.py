"""GC API endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["gc"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class GCRequest(BaseModel):
    grace_hours: int = 24
    dry_run: bool = False


@router.post("/{repo}/gc")
async def gc_endpoint(
    repo: str,
    body: GCRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    from dit.core.gc import gc

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(Ref.target_hash).where(Ref.repo_id == r.id)
    )
    ref_hashes = [row[0] for row in result.all()]

    gc_result = gc(
        store,
        ref_hashes,
        grace_seconds=body.grace_hours * 3600,
        dry_run=body.dry_run,
    )

    return {
        "live_counts": gc_result.live_counts,
        "deleted_counts": gc_result.deleted_counts,
        "skipped_counts": gc_result.skipped_counts,
        "total_scanned": gc_result.total_scanned,
        "total_deleted": gc_result.total_deleted,
        "tmp_deleted": gc_result.tmp_deleted,
        "errors": gc_result.errors,
    }
