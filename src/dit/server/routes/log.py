from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["log"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 200


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/log")
async def get_log(
    repo: str,
    request: Request,
    ref: str = Query(..., description="Ref name, e.g. 'heads/main' or a commit hash"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit

    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == ref)
    )
    ref_obj = result.scalar_one_or_none()
    if ref_obj is None:
        if store.read("commits", ref) is None:
            raise HTTPException(status_code=404, detail=f"Ref '{ref}' not found")
        start_hash = ref
    else:
        start_hash = ref_obj.target_hash

    commits = []
    visited: set[str] = set()
    queue = [start_hash]

    while queue and len(commits) < offset + limit:
        chash = queue.pop(0)
        if chash in visited:
            continue
        visited.add(chash)
        data = store.read("commits", chash)
        if data is None:
            break
        commit = deserialize_commit(data)
        commits.append({
            "commit_hash": chash,
            "tree_hash": commit.tree_hash,
            "parent_hashes": commit.parent_hashes,
            "author": commit.author,
            "message": commit.message,
            "timestamp": commit.timestamp,
        })
        if commit.parent_hashes:
            queue.append(commit.parent_hashes[0])

    page = commits[offset: offset + limit]
    return {
        "ref": ref,
        "total_fetched": len(commits),
        "offset": offset,
        "limit": len(page),
        "commits": page,
    }
