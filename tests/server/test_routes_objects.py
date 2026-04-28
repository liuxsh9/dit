import base64
import hashlib

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from dit.server.app import create_app
from dit.server.auth import get_session
from dit.server.config import ServerSettings
from dit.server.database import create_session_factory
from dit.server.models import Token

PAYLOAD = b"hello world data row"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD).hexdigest()

READER_TOKEN_RAW = "test-reader-token"


@pytest_asyncio.fixture
async def reader_client(engine, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(data_dir),
    )
    app = create_app(settings=settings)
    factory = create_session_factory(engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    async with factory() as s:
        token_hash = hashlib.sha256(READER_TOKEN_RAW.encode()).hexdigest()
        t = Token(
            token_hash=token_hash,
            label="test-reader",
            permissions="read",
            role="reader",
        )
        s.add(t)
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {READER_TOKEN_RAW}"},
    ) as c:
        yield c


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


# ---------------------------------------------------------------------------
# Batch-exists hash limit
# ---------------------------------------------------------------------------


async def test_batch_exists_hash_limit_rejected(client: AsyncClient) -> None:
    """Sending more than 10 000 hashes must be rejected with 400."""
    await _create_repo(client)
    hashes = [f"{i:064x}" for i in range(10_001)]
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "rows", "hashes": hashes},
    )
    assert response.status_code == 400
    assert "exceeds limit" in response.json()["detail"].lower()


async def test_batch_exists_within_limit_accepted(client: AsyncClient) -> None:
    """A small number of hashes should be accepted normally."""
    await _create_repo(client)
    hashes = [f"{i:064x}" for i in range(5)]
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "rows", "hashes": hashes},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Batch-upload item limit
# ---------------------------------------------------------------------------


async def test_batch_upload_item_limit_rejected(client: AsyncClient) -> None:
    """Sending more than 200 items must be rejected with 400."""
    await _create_repo(client)
    items = [{"hash": "a" * 64, "data_b64": "aGVsbG8="} for _ in range(201)]
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload",
        json={"obj_type": "rows", "items": items},
    )
    assert response.status_code == 400
    assert "exceeds limit" in response.json()["detail"].lower()


async def test_batch_upload_within_limit_accepted(client: AsyncClient) -> None:
    """A small batch of valid items should be accepted."""
    await _create_repo(client)
    data = b"x"
    h = hashlib.sha256(data).hexdigest()
    item = {"hash": h, "data_b64": base64.b64encode(data).decode()}
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload",
        json={"obj_type": "rows", "items": [item]},
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1


# ---------------------------------------------------------------------------
# Permission enforcement — write endpoints require push, not just read
# ---------------------------------------------------------------------------


async def test_upload_object_requires_push_permission(
    reader_client: AsyncClient,
) -> None:
    """A reader token must get 403 on single-object upload."""
    # Create repo via reader_client's own app — repo creation also needs push,
    # so we expect 403 there too.  Instead, just hit the upload endpoint directly;
    # the permission check fires before the repo-existence check.
    response = await reader_client.post(
        f"/api/v1/repos/any-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    assert response.status_code == 403


async def test_batch_upload_requires_push_permission(
    reader_client: AsyncClient,
) -> None:
    """A reader token must get 403 on batch upload."""
    data = b"x"
    h = hashlib.sha256(data).hexdigest()
    item = {"hash": h, "data_b64": base64.b64encode(data).decode()}
    response = await reader_client.post(
        "/api/v1/repos/any-repo/objects/batch-upload",
        json={"obj_type": "rows", "items": [item]},
    )
    assert response.status_code == 403


async def test_download_object_allowed_for_reader(
    client: AsyncClient, reader_client: AsyncClient,
) -> None:
    """A reader token should be able to download objects (read is allowed)."""
    # Use admin client to set up data
    await _create_repo(client)
    await client.post(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
        content=PAYLOAD,
    )
    # Reader downloads
    response = await reader_client.get(
        f"/api/v1/repos/obj-repo/objects/rows/{PAYLOAD_HASH}",
    )
    assert response.status_code == 200
    assert response.content == PAYLOAD


async def test_batch_exists_allowed_for_reader(
    client: AsyncClient, reader_client: AsyncClient,
) -> None:
    """A reader token should be able to call batch-exists (read is allowed)."""
    await _create_repo(client)
    response = await reader_client.post(
        "/api/v1/repos/obj-repo/objects/batch-exists",
        json={"obj_type": "rows", "hashes": [PAYLOAD_HASH]},
    )
    assert response.status_code == 200
