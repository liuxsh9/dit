import hashlib

import pytest
from httpx import AsyncClient

PAYLOAD = b"hello world data row"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()


async def _create_repo(client: AsyncClient, name: str = "obj-repo") -> None:
    r = await client.post("/api/v1/repos", json={"name": name})
    assert r.status_code == 201


async def test_upload_object(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 204


async def test_upload_idempotent(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 204


async def test_upload_hash_mismatch_returns_400(client: AsyncClient) -> None:
    await _create_repo(client)
    wrong_hash = "0" * 64
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{wrong_hash}",
        content=PAYLOAD,
    )
    assert response.status_code == 400


async def test_download_object(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    response = await client.get(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}")
    assert response.status_code == 200
    assert response.content == PAYLOAD


async def test_download_missing_returns_404(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.get(f"/api/v1/repos/obj-repo/objects/rows/{'0' * 64}")
    assert response.status_code == 404


async def test_batch_exists(client: AsyncClient) -> None:
    await _create_repo(client)
    await client.post(f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}", content=PAYLOAD)
    missing_hash = "f" * 64
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "rows", "hashes": [PAYLOAD_HASH, missing_hash]},
    )
    assert response.status_code == 200
    data = response.json()["exists"]
    assert data[PAYLOAD_HASH] is True
    assert data[missing_hash] is False


async def test_upload_repo_not_found_returns_404(client: AsyncClient) -> None:
    response = await client.post(
        f"/api/v1/repos/no-such-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 404
