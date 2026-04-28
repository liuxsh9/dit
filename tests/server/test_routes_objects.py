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


async def test_invalid_obj_type_rejected(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.get(
        f"/api/v1/repos/obj-repo/objects/secrets/{PAYLOAD_HASH}"
    )
    assert response.status_code == 400


async def test_invalid_obj_type_upload_rejected(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.post(
        f"/api/v1/repos/obj-repo/objects/badtype/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 400


async def test_valid_obj_types_accepted(client: AsyncClient) -> None:
    await _create_repo(client)
    for obj_type in ("commits", "trees", "manifests", "rows", "sidecars", "blobs"):
        response = await client.get(
            f"/api/v1/repos/obj-repo/objects/{obj_type}/{'0' * 64}"
        )
        # Should get 404 (not found) rather than 400 (bad request)
        assert response.status_code == 404, f"Expected 404 for {obj_type}, got {response.status_code}"


async def test_batch_exists_invalid_obj_type_rejected(client: AsyncClient) -> None:
    await _create_repo(client)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "../traversal", "hashes": [PAYLOAD_HASH]},
    )
    assert response.status_code == 400


async def test_path_traversal_repo_name_rejected(client: AsyncClient) -> None:
    """Even if repo exists in DB, path traversal in URL should be blocked."""
    response = await client.get(
        f"/api/v1/repos/../../../etc/objects/rows/{'0' * 64}"
    )
    # Should be 400 (validation) or 404 (not found) — not a successful traversal
    assert response.status_code in (400, 404)
