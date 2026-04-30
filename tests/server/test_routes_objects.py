import base64
import hashlib

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
# Batch-download
# ---------------------------------------------------------------------------


async def _upload_object(client: AsyncClient, obj_type: str, data: bytes) -> str:
    """Upload an object and return its hash."""
    h = hashlib.sha256(data).hexdigest()
    r = await client.post(f"/api/v1/repos/obj-repo/objects/{obj_type}/{h}", content=data)
    assert r.status_code == 204
    return h


async def test_batch_download_returns_objects(client: AsyncClient) -> None:
    """Upload 3 objects, batch-download them, verify data matches."""
    await _create_repo(client)
    payloads = [b"row-data-one", b"row-data-two", b"row-data-three"]
    hashes = []
    for p in payloads:
        h = await _upload_object(client, "rows", p)
        hashes.append(h)

    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-download",
        json={"obj_type": "rows", "hashes": hashes},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["missing"] == []

    downloaded = {
        item["hash"]: base64.b64decode(item["data_b64"]) for item in body["items"]
    }
    for p, h in zip(payloads, hashes):
        assert downloaded[h] == p


async def test_batch_download_reports_missing(client: AsyncClient) -> None:
    """Request hashes that don't exist, verify they appear in missing."""
    await _create_repo(client)
    missing_hashes = [f"{i:064x}" for i in range(3)]
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-download",
        json={"obj_type": "rows", "hashes": missing_hashes},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert sorted(body["missing"]) == sorted(missing_hashes)


async def test_batch_download_mixed(client: AsyncClient) -> None:
    """Some exist, some don't -- verify items and missing are correct."""
    await _create_repo(client)
    existing_hash = await _upload_object(client, "rows", b"exists-data")
    missing_hash = "f" * 64

    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-download",
        json={"obj_type": "rows", "hashes": [existing_hash, missing_hash]},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["hash"] == existing_hash
    assert base64.b64decode(body["items"][0]["data_b64"]) == b"exists-data"
    assert body["missing"] == [missing_hash]


async def test_batch_download_invalid_obj_type(client: AsyncClient) -> None:
    """Invalid obj_type should return 400."""
    await _create_repo(client)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-download",
        json={"obj_type": "secrets", "hashes": ["a" * 64]},
    )
    assert response.status_code == 400


async def test_batch_download_exceeds_limit(client: AsyncClient) -> None:
    """Request 201 hashes should return 400."""
    await _create_repo(client)
    hashes = [f"{i:064x}" for i in range(201)]
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-download",
        json={"obj_type": "rows", "hashes": hashes},
    )
    assert response.status_code == 400
    assert "exceeds limit" in response.json()["detail"].lower()


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


# ---------------------------------------------------------------------------
# Binary batch-upload endpoint
# ---------------------------------------------------------------------------


def _build_bin_payload(obj_type: str, items: list[tuple[str, bytes]]) -> bytes:
    """Build the binary wire format for batch-upload-bin."""
    buf = bytearray()
    obj_type_bytes = obj_type.encode()
    buf.append(len(obj_type_bytes))
    buf.extend(obj_type_bytes)
    buf.extend(len(items).to_bytes(4, "big"))
    for h, data in items:
        buf.extend(h.encode())  # 64 bytes hex ASCII
        buf.extend(len(data).to_bytes(4, "big"))
        buf.extend(data)
    return bytes(buf)


async def test_batch_upload_bin_basic(client: AsyncClient) -> None:
    """Upload 3 objects via binary endpoint, verify they're stored."""
    await _create_repo(client)
    payloads = [b"row-data-one", b"row-data-two", b"row-data-three"]
    items = [(hashlib.sha256(p).hexdigest(), p) for p in payloads]

    body = _build_bin_payload("rows", items)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload-bin",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["accepted"] == 3
    assert data["errors"] == []

    # Verify objects are actually stored by downloading them
    for h, payload in items:
        r = await client.get(f"/api/v1/repos/obj-repo/objects/rows/{h}")
        assert r.status_code == 200
        assert r.content == payload


async def test_batch_upload_bin_hash_mismatch(client: AsyncClient) -> None:
    """Send wrong hash, verify error reported but request succeeds."""
    await _create_repo(client)
    data = b"some data"
    wrong_hash = "0" * 64
    items = [(wrong_hash, data)]

    body = _build_bin_payload("rows", items)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload-bin",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result["accepted"] == 0
    assert len(result["errors"]) == 1
    assert "hash mismatch" in result["errors"][0]


async def test_batch_upload_bin_exceeds_limit(client: AsyncClient) -> None:
    """Send 201 items, verify 400."""
    await _create_repo(client)
    data = b"x"
    h = hashlib.sha256(data).hexdigest()
    items = [(h, data)] * 201

    body = _build_bin_payload("rows", items)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload-bin",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400
    assert "too many" in response.json()["detail"].lower()


async def test_batch_upload_bin_invalid_obj_type(client: AsyncClient) -> None:
    """Invalid obj_type should return 400."""
    await _create_repo(client)
    data = b"x"
    h = hashlib.sha256(data).hexdigest()
    items = [(h, data)]

    body = _build_bin_payload("secrets", items)
    response = await client.post(
        "/api/v1/repos/obj-repo/objects/batch-upload-bin",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 400


async def test_batch_upload_bin_requires_push_permission(
    reader_client: AsyncClient,
) -> None:
    """A reader token must get 403 on binary batch upload."""
    data = b"x"
    h = hashlib.sha256(data).hexdigest()
    body = _build_bin_payload("rows", [(h, data)])
    response = await reader_client.post(
        "/api/v1/repos/any-repo/objects/batch-upload-bin",
        content=body,
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 403
