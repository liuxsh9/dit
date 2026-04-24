from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["diff"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class DiffRequest(BaseModel):
    old_commit: str
    new_commit: str
    path: Optional[str] = None
    include_rows: bool = False
    offset: int = 0
    limit: int = 100


@router.post("/{repo}/diff")
async def diff_commits(
    repo: str,
    body: DiffRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit, deserialize_manifest, Manifest
    from dit.core.diff import diff_manifests
    from dit.core.tree_walker import flatten_tree

    old_commit_data = store.read("commits", body.old_commit)
    if old_commit_data is None:
        raise HTTPException(status_code=404, detail=f"Old commit '{body.old_commit[:8]}' not found")

    new_commit_data = store.read("commits", body.new_commit)
    if new_commit_data is None:
        raise HTTPException(status_code=404, detail=f"New commit '{body.new_commit[:8]}' not found")

    old_commit = deserialize_commit(old_commit_data)
    new_commit = deserialize_commit(new_commit_data)

    old_flat = flatten_tree(store, old_commit.tree_hash)
    new_flat = flatten_tree(store, new_commit.tree_hash)

    def manifest_map(flat: dict) -> dict[str, str]:
        return {
            path: obj_hash
            for path, (obj_type, obj_hash) in flat.items()
            if obj_type == "manifest"
        }

    old_manifests = manifest_map(old_flat)
    new_manifests = manifest_map(new_flat)

    all_paths = sorted(set(old_manifests) | set(new_manifests))
    if body.path:
        clean = body.path.strip("/")
        all_paths = [p for p in all_paths if p == clean]

    file_diffs = []
    for path in all_paths:
        old_m_hash = old_manifests.get(path)
        new_m_hash = new_manifests.get(path)

        if old_m_hash == new_m_hash:
            continue

        old_manifest = Manifest(entries=[])
        if old_m_hash:
            old_m_data = store.read("manifests", old_m_hash)
            if old_m_data:
                old_manifest = deserialize_manifest(old_m_data)

        new_manifest = Manifest(entries=[])
        if new_m_hash:
            new_m_data = store.read("manifests", new_m_hash)
            if new_m_data:
                new_manifest = deserialize_manifest(new_m_data)

        result = diff_manifests(old_manifest, new_manifest)

        file_entry: dict = {
            "path": path,
            "added": len(result.added),
            "removed": len(result.removed),
            "refreshed": len(result.refreshed),
            "old_total": len(old_manifest.entries),
            "new_total": len(new_manifest.entries),
        }

        if body.include_rows:
            import json as _json

            def _row_entry(rh: str, position: int) -> dict:
                content = None
                raw = store.read("rows", rh)
                if raw is not None:
                    try:
                        content = _json.loads(raw)
                    except Exception:
                        content = None
                return {"row_hash": rh, "position": position, "content": content}

            added_page = result.added[body.offset: body.offset + body.limit]
            removed_page = result.removed[body.offset: body.offset + body.limit]

            file_entry["added_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(added_page)
            ]
            file_entry["removed_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(removed_page)
            ]
            file_entry["refreshed_rows"] = [
                {
                    "old_row_hash": old_rh,
                    "new_row_hash": new_rh,
                    "query_fingerprint": qfp,
                }
                for old_rh, new_rh, qfp in result.refreshed[body.offset: body.offset + body.limit]
            ]

        file_diffs.append(file_entry)

    return {
        "old_commit": body.old_commit,
        "new_commit": body.new_commit,
        "files": file_diffs,
    }
