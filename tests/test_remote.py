"""Tests for RemoteClient using httpx.MockTransport."""
from __future__ import annotations

import hashlib
import json

import httpx

from dit.core.remote import RemoteClient


def _json_response(data, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        content=json.dumps(data).encode(),
    )


def _make_client(handler) -> RemoteClient:
    transport = httpx.MockTransport(handler)
    rc = RemoteClient.__new__(RemoteClient)
    rc.base_url = "http://test"
    rc.repo = "my-repo"
    rc.client = httpx.Client(transport=transport, trust_env=False)
    return rc


def test_init_omits_authorization_header_when_token_empty() -> None:
    rc = RemoteClient("http://test", token="", repo="my-repo")
    assert "Authorization" not in rc.client.headers
    rc.client.close()


def test_init_sets_authorization_header_when_token_present() -> None:
    rc = RemoteClient("http://test", token="secret", repo="my-repo")
    assert rc.client.headers["Authorization"] == "token secret"
    rc.client.close()


def test_create_repo() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/repos"
        body = json.loads(request.content)
        assert body["name"] == "my-repo"
        return _json_response({"id": 1, "name": "my-repo"}, 201)

    rc = _make_client(handler)
    result = rc.create_repo("my-repo")
    assert result["name"] == "my-repo"
    assert result["id"] == 1


def test_list_repos() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v1/repos"
        return _json_response([{"id": 1, "name": "r1"}, {"id": 2, "name": "r2"}])

    rc = _make_client(handler)
    repos = rc.list_repos()
    assert len(repos) == 2
    assert repos[0]["name"] == "r1"


def test_get_ref_found() -> None:
    hash_val = "a" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs/heads/main"
        return _json_response({"name": "heads/main", "target_hash": hash_val})

    rc = _make_client(handler)
    result = rc.get_ref("heads", "main")
    assert result == hash_val


def test_get_ref_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail":"not found"}')

    rc = _make_client(handler)
    result = rc.get_ref("heads", "main")
    assert result is None


def test_list_refs() -> None:
    refs = [{"name": "heads/main", "target_hash": "a" * 64}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs"
        return _json_response(refs)

    rc = _make_client(handler)
    result = rc.list_refs()
    assert result == refs


def test_cas_ref_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/refs/heads/main"
        body = json.loads(request.content)
        assert body["old"] is None
        assert body["new"] == "b" * 64
        return _json_response({"name": "heads/main", "target_hash": "b" * 64})

    rc = _make_client(handler)
    ok = rc.cas_ref("heads", "main", old=None, new="b" * 64)
    assert ok is True


def test_cas_ref_mismatch_returns_false() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, content=b'{"detail":"CAS mismatch"}')

    rc = _make_client(handler)
    ok = rc.cas_ref("heads", "main", old="a" * 64, new="b" * 64)
    assert ok is False


def test_upload_object() -> None:
    payload = b"row data"
    hash_hex = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/repos/my-repo/objects/rows/{hash_hex}"
        assert request.method == "POST"
        assert request.content == payload
        return httpx.Response(204)

    rc = _make_client(handler)
    rc.upload_object("rows", hash_hex, payload)


def test_download_object() -> None:
    payload = b"row data"
    hash_hex = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/repos/my-repo/objects/rows/{hash_hex}"
        assert request.method == "GET"
        return httpx.Response(200, content=payload)

    rc = _make_client(handler)
    result = rc.download_object("rows", hash_hex)
    assert result == payload


def test_download_object_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, content=b'{"detail":"not found"}')

    rc = _make_client(handler)
    result = rc.download_object("rows", "0" * 64)
    assert result is None


def test_batch_exists() -> None:
    h1, h2 = "a" * 64, "b" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/repos/my-repo/objects/batch-exists"
        body = json.loads(request.content)
        assert body["obj_type"] == "rows"
        assert set(body["hashes"]) == {h1, h2}
        return _json_response({"exists": {h1: True, h2: False}})

    rc = _make_client(handler)
    result = rc.batch_exists("rows", [h1, h2])
    assert result[h1] is True
    assert result[h2] is False
