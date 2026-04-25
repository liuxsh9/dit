"""Single-file export endpoint."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["export"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/export/{commit_hash}/{file_path:path}")
async def export_file(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    format: str = Query(default="jsonl", description="Output format: jsonl or csv"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Export a single file from a commit as raw JSONL or CSV content."""
    from dit.core.export import export_commit

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    if format not in ("jsonl", "csv"):
        raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Expected 'jsonl' or 'csv'.")

    clean_path = file_path.lstrip("/")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        try:
            export_commit(
                store,
                commit_hash,
                output_dir,
                file_filter=clean_path,
                fmt=format,
                include_meta=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        dest = output_dir / clean_path
        if not dest.exists():
            raise HTTPException(status_code=404, detail=f"Export produced no file for '{clean_path}'")

        content = dest.read_text(encoding="utf-8")

    if format == "csv":
        media_type = "text/csv"
    else:
        media_type = "application/x-ndjson"

    return PlainTextResponse(content=content, media_type=media_type)
