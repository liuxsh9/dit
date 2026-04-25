"""Fsck API endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["fsck"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class FsckRequest(BaseModel):
    check_hashes: bool = True
    check_graph: bool = True


@router.post("/{repo}/fsck")
async def fsck_endpoint(
    repo: str,
    body: FsckRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    from dit.core.fsck import fsck

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(Ref.target_hash).where(Ref.repo_id == r.id)
    )
    ref_hashes = [row[0] for row in result.all()]

    fsck_result = fsck(
        store,
        ref_hashes,
        check_hashes=body.check_hashes,
        check_graph=body.check_graph,
    )

    return {
        "checked_objects": fsck_result.checked_objects,
        "errors": [
            {"severity": e.severity, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "message": e.message}
            for e in fsck_result.errors
        ],
        "warnings": [
            {"severity": w.severity, "obj_type": w.obj_type, "obj_hash": w.obj_hash, "message": w.message}
            for w in fsck_result.warnings
        ],
        "total_checked": fsck_result.total_checked,
        "total_errors": fsck_result.total_errors,
        "total_warnings": fsck_result.total_warnings,
    }
