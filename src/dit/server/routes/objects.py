import base64
import hashlib
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.core.store import ObjectStore
from dit.server.auth import get_session, require_permission, verify_token
from dit.server.models import Repo, Token

router = APIRouter(prefix="/api/v1/repos", tags=["objects"])

_VALID_OBJ_TYPES = {"commits", "trees", "manifests", "rows", "sidecars", "blobs"}


def _validate_obj_type(obj_type: str) -> None:
    if obj_type not in _VALID_OBJ_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid object type: must be one of {sorted(_VALID_OBJ_TYPES)}",
        )


async def _get_repo(repo_name: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo_name))
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


def _store_for_repo(request: Request, repo_name: str) -> ObjectStore:
    data_dir = Path(request.app.state.data_dir).resolve()
    repo_path = (data_dir / "repos" / repo_name / "objects").resolve()
    if not str(repo_path).startswith(str(data_dir) + "/"):
        raise HTTPException(status_code=400, detail="Invalid repository name")
    return ObjectStore(repo_path)


class BatchExistsIn(BaseModel):
    obj_type: str
    hashes: list[str]


class BatchExistsOut(BaseModel):
    exists: dict[str, bool]


_MAX_BATCH_EXISTS_HASHES = 10_000


@router.post("/{repo}/objects/batch-exists", response_model=BatchExistsOut)
async def batch_exists(
    repo: str,
    body: BatchExistsIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> BatchExistsOut:
    _validate_obj_type(body.obj_type)
    if len(body.hashes) > _MAX_BATCH_EXISTS_HASHES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many hashes: {len(body.hashes)} exceeds limit of {_MAX_BATCH_EXISTS_HASHES}",
        )
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    result = store.batch_exists(body.obj_type, body.hashes)
    return BatchExistsOut(exists=result)


class BatchUploadItem(BaseModel):
    hash: str
    data_b64: str  # base64-encoded object data


class BatchUploadIn(BaseModel):
    obj_type: str
    items: list[BatchUploadItem]

    class Config:
        max_anystr_length = 20_000_000  # ~15MB base64 payload per item

_MAX_BATCH_ITEMS = 200


class BatchUploadOut(BaseModel):
    accepted: int
    errors: list[str]


# ---------------------------------------------------------------------------
# Batch-download models
# ---------------------------------------------------------------------------


class BatchDownloadIn(BaseModel):
    obj_type: str
    hashes: list[str]


class BatchDownloadItem(BaseModel):
    hash: str
    data_b64: str


class BatchDownloadOut(BaseModel):
    items: list[BatchDownloadItem]
    missing: list[str]


_MAX_BATCH_DOWNLOAD_HASHES = 200


@router.post("/{repo}/objects/batch-upload", response_model=BatchUploadOut)
async def batch_upload(
    repo: str,
    body: BatchUploadIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(require_permission("push")),
) -> BatchUploadOut:
    _validate_obj_type(body.obj_type)
    if len(body.items) > _MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many items: {len(body.items)} exceeds limit of {_MAX_BATCH_ITEMS}",
        )
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    accepted = 0
    errors: list[str] = []
    for item in body.items:
        try:
            data = base64.b64decode(item.data_b64)
        except Exception:
            errors.append(f"{item.hash}: invalid base64")
            continue
        computed = hashlib.sha256(data).hexdigest()
        if computed != item.hash:
            errors.append(f"{item.hash}: hash mismatch (computed {computed})")
            continue
        store.write(body.obj_type, data)
        accepted += 1
    return BatchUploadOut(accepted=accepted, errors=errors)


@router.post("/{repo}/objects/batch-upload-bin")
async def batch_upload_bin(
    repo: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(require_permission("push")),
) -> Response:
    """Upload multiple objects using binary wire format (no base64 overhead)."""
    await _get_repo(repo, session)
    body = await request.body()
    store = _store_for_repo(request, repo)

    offset = 0
    # Read obj_type
    obj_type_len = body[offset]
    offset += 1
    obj_type = body[offset : offset + obj_type_len].decode()
    offset += obj_type_len
    _validate_obj_type(obj_type)

    # Read item count
    item_count = int.from_bytes(body[offset : offset + 4], "big")
    offset += 4
    if item_count > _MAX_BATCH_ITEMS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many items: {item_count} exceeds limit of {_MAX_BATCH_ITEMS}",
        )

    accepted = 0
    errors: list[str] = []
    for _ in range(item_count):
        hash_hex = body[offset : offset + 64].decode()
        offset += 64
        data_len = int.from_bytes(body[offset : offset + 4], "big")
        offset += 4
        data = body[offset : offset + data_len]
        offset += data_len

        computed = hashlib.sha256(data).hexdigest()
        if computed != hash_hex:
            errors.append(f"{hash_hex}: hash mismatch (computed {computed})")
            continue
        store.write(obj_type, data)
        accepted += 1

    return Response(
        content=json.dumps({"accepted": accepted, "errors": errors}),
        media_type="application/json",
    )


@router.post("/{repo}/objects/batch-download", response_model=BatchDownloadOut)
async def batch_download(
    repo: str,
    body: BatchDownloadIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> BatchDownloadOut:
    _validate_obj_type(body.obj_type)
    if len(body.hashes) > _MAX_BATCH_DOWNLOAD_HASHES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many hashes: {len(body.hashes)} exceeds limit of {_MAX_BATCH_DOWNLOAD_HASHES}",
        )
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    items: list[BatchDownloadItem] = []
    missing: list[str] = []
    for h in body.hashes:
        data = store.read(body.obj_type, h)
        if data is None:
            missing.append(h)
        else:
            items.append(BatchDownloadItem(
                hash=h,
                data_b64=base64.b64encode(data).decode("ascii"),
            ))
    return BatchDownloadOut(items=items, missing=missing)


@router.get("/{repo}/objects/{obj_type}/{hash}", response_class=Response)
async def download_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(verify_token),
) -> Response:
    _validate_obj_type(obj_type)
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)
    data = store.read(obj_type, hash)
    if data is None:
        raise HTTPException(status_code=404, detail="Object not found")
    return Response(content=data, media_type="application/octet-stream")


@router.post("/{repo}/objects/{obj_type}/{hash}", status_code=status.HTTP_204_NO_CONTENT)
async def upload_object(
    repo: str,
    obj_type: str,
    hash: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token: Token = Depends(require_permission("push")),
) -> None:
    _validate_obj_type(obj_type)
    await _get_repo(repo, session)
    body = await request.body()
    computed = hashlib.sha256(body).hexdigest()
    if computed != hash:
        raise HTTPException(
            status_code=400,
            detail=f"Hash mismatch: path has {hash}, body hashes to {computed}",
        )
    store = _store_for_repo(request, repo)
    store.write(obj_type, body)
