from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["manifest"])

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/manifest/{commit_hash}/{path:path}")
async def get_manifest(
    repo: str,
    commit_hash: str,
    path: str,
    request: Request,
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, description="Max rows to return (clamped to 500)"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit, deserialize_manifest
    from dit.core.tree_walker import flatten_tree

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_path = path.strip("/")
    if clean_path not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean_path}' not found")

    obj_type, obj_hash = flat[clean_path]
    if obj_type != "manifest":
        raise HTTPException(
            status_code=404,
            detail=f"Path '{clean_path}' is not a manifest (type={obj_type})",
        )

    manifest_data = store.read("manifests", obj_hash)
    if manifest_data is None:
        raise HTTPException(status_code=404, detail="Manifest object not found in store")

    manifest = deserialize_manifest(manifest_data)
    total = len(manifest.entries)
    limit = min(limit, _MAX_LIMIT)
    page = manifest.entries[offset: offset + limit]

    return {
        "commit_hash": commit_hash,
        "path": clean_path,
        "total": total,
        "offset": offset,
        "limit": len(page),
        "entries": [
            {"row_hash": e.row_hash, "query_fingerprint": e.query_fingerprint}
            for e in page
        ],
    }
