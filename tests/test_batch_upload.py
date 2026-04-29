"""Tests for batch upload functionality."""
import base64
import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dit.core.remote import RemoteClient
from dit.core.store import ObjectStore


class TestRemoteClientBatchUpload:
    def test_upload_batch_sends_correct_payload(self):
        """upload_batch tries binary first, sending correct binary payload."""
        client = RemoteClient("http://localhost:8000", token="test", repo="test-repo")

        data1 = b"hello"
        hash1 = hashlib.sha256(data1).hexdigest()
        data2 = b"world"
        hash2 = hashlib.sha256(data2).hexdigest()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"accepted": 2, "errors": []}
        mock_response.raise_for_status = MagicMock()

        client.client.post = MagicMock(return_value=mock_response)

        result = client.upload_batch("rows", [(hash1, data1), (hash2, data2)])

        assert result["accepted"] == 2
        assert result["errors"] == []

        # Verify binary endpoint was called
        call_args = client.client.post.call_args
        assert "batch-upload-bin" in call_args[0][0]
        assert call_args[1]["headers"]["Content-Type"] == "application/octet-stream"

    def test_upload_batch_json_sends_correct_payload(self):
        """_upload_batch_json sends base64-encoded items to batch-upload endpoint."""
        client = RemoteClient("http://localhost:8000", token="test", repo="test-repo")

        data1 = b"hello"
        hash1 = hashlib.sha256(data1).hexdigest()
        data2 = b"world"
        hash2 = hashlib.sha256(data2).hexdigest()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"accepted": 2, "errors": []}
        mock_response.raise_for_status = MagicMock()

        client.client.post = MagicMock(return_value=mock_response)

        result = client._upload_batch_json("rows", [(hash1, data1), (hash2, data2)])

        assert result["accepted"] == 2
        assert result["errors"] == []

        call_args = client.client.post.call_args
        payload = call_args[1]["json"]
        assert payload["obj_type"] == "rows"
        assert len(payload["items"]) == 2
        assert payload["items"][0]["hash"] == hash1
        assert base64.b64decode(payload["items"][0]["data_b64"]) == data1

    def test_upload_batch_fallback_on_404(self):
        """If binary endpoint returns 404, fall back to JSON, then individual."""
        client = RemoteClient("http://localhost:8000", token="test", repo="test-repo")

        data1 = b"hello"
        hash1 = hashlib.sha256(data1).hexdigest()

        mock_404 = MagicMock()
        mock_404.status_code = 404
        mock_404.raise_for_status = MagicMock(
            side_effect=Exception("404")
        )

        mock_204 = MagicMock()
        mock_204.status_code = 204
        mock_204.raise_for_status = MagicMock()

        urls_called = []

        def mock_post(url, **kwargs):
            urls_called.append(url)
            if "batch-upload-bin" in url:
                return mock_404
            if "batch-upload" in url:
                return mock_404
            return mock_204

        client.client.post = mock_post

        result = client.upload_batch("rows", [(hash1, data1)])
        assert result["accepted"] == 1
        # binary attempt -> JSON batch attempt (404) -> individual fallback
        assert len(urls_called) == 3


class TestBatchUploadIntegration:
    def test_batch_upload_stores_objects(self, tmp_path: Path):
        """Verify batch-uploaded objects are correctly stored."""
        store = ObjectStore(tmp_path / "objects")

        data1 = b'{"row": 1}'
        hash1 = hashlib.sha256(data1).hexdigest()
        data2 = b'{"row": 2}'
        hash2 = hashlib.sha256(data2).hexdigest()

        # Simulate what the server endpoint does
        for h, d in [(hash1, data1), (hash2, data2)]:
            computed = hashlib.sha256(d).hexdigest()
            assert computed == h
            store.write("rows", d)

        assert store.read("rows", hash1) == data1
        assert store.read("rows", hash2) == data2
