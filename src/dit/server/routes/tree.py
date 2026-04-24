from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["tree"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/tree/{commit_hash}/{path:path}")
async def get_tree(
    repo: str,
    commit_hash: str,
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)

    from dit.core.tree_walker import resolve_path
    entries = resolve_path(store, commit.tree_hash, path.strip("/"))
    if entries is None:
        raise HTTPException(status_code=404, detail=f"Path '{path}' not found in tree")

    return {"commit_hash": commit_hash, "path": path.strip("/"), "entries": entries}
