# src/dit/server/routes/validate_api.py
"""Validate endpoint and CI checks endpoints."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import CICheck, Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["validate"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class ValidateRequest(BaseModel):
    ref: str = "heads/main"


class CheckReportRequest(BaseModel):
    commit_hash: str
    check_name: str
    status: str  # "pending" | "pass" | "fail"
    details: dict[str, Any] | None = None


@router.post("/{repo}/validate")
async def repo_validate_endpoint(
    repo: str,
    body: ValidateRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Run validation rules against all JSONL rows in a commit."""
    from dit.core.validate import validate_commit

    r = await _get_repo(repo, session)

    # Resolve ref to commit hash
    if len(body.ref) == 64 and all(c in "0123456789abcdef" for c in body.ref):
        commit_hash = body.ref
    else:
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.ref)
        )
        ref_obj = result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.ref}' not found")
        commit_hash = ref_obj.target_hash

    store = _store_for_repo(request, repo)

    # Load rules from the committed tree (falls back to defaults if not present)
    rules = _load_rules_from_store(store, commit_hash)

    try:
        result = validate_commit(store, commit_hash, rules)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return result


def _load_rules_from_store(store, commit_hash: str) -> dict:
    """Read .ditvalidate.yaml from the committed tree via the object store.

    Falls back to default rules if the file is not present in the tree.
    """
    from dit.core.objects import deserialize_commit
    from dit.core.tree_walker import flatten_tree
    from dit.core.validate import load_rules

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        # validate_commit will raise FileNotFoundError; return defaults here
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    # Look for a blob named ".ditvalidate.yaml" at the repo root
    entry = flat.get(".ditvalidate.yaml")
    if entry is None:
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    obj_type, obj_hash, _ = entry
    if obj_type != "blob":
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    blob_data = store.read("blobs", obj_hash)
    if blob_data is None:
        with tempfile.TemporaryDirectory() as td:
            return load_rules(Path(td))

    with tempfile.TemporaryDirectory() as td:
        rules_path = Path(td) / ".ditvalidate.yaml"
        rules_path.write_bytes(blob_data)
        return load_rules(Path(td))


@router.post("/{repo}/checks", status_code=201)
async def report_check_endpoint(
    repo: str,
    body: CheckReportRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Report or update a CI check result for a commit."""
    r = await _get_repo(repo, session)

    # Upsert: look for existing row
    result = await session.execute(
        select(CICheck).where(
            CICheck.repo_id == r.id,
            CICheck.commit_hash == body.commit_hash,
            CICheck.check_name == body.check_name,
        )
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.status = body.status
        existing.details_json = body.details
        await session.commit()
        await session.refresh(existing)
        check = existing
    else:
        check = CICheck(
            repo_id=r.id,
            commit_hash=body.commit_hash,
            check_name=body.check_name,
            status=body.status,
            details_json=body.details,
        )
        session.add(check)
        await session.commit()
        await session.refresh(check)

    return {
        "id": check.id,
        "repo_id": check.repo_id,
        "commit_hash": check.commit_hash,
        "check_name": check.check_name,
        "status": check.status,
        "details": check.details_json,
        "created_at": check.created_at.isoformat() if check.created_at else None,
        "updated_at": check.updated_at.isoformat() if check.updated_at else None,
    }


@router.get("/{repo}/checks/{commit}")
async def get_checks_endpoint(
    repo: str,
    commit: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Get all CI checks for a commit."""
    r = await _get_repo(repo, session)

    result = await session.execute(
        select(CICheck).where(
            CICheck.repo_id == r.id,
            CICheck.commit_hash == commit,
        ).order_by(CICheck.id)
    )
    checks = result.scalars().all()

    return {
        "commit_hash": commit,
        "checks": [
            {
                "id": c.id,
                "check_name": c.check_name,
                "status": c.status,
                "details": c.details_json,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "updated_at": c.updated_at.isoformat() if c.updated_at else None,
            }
            for c in checks
        ],
    }
