from __future__ import annotations

import base64

import httpx


class RemoteClient:
    """Synchronous HTTP client for the Dit server API via Forgejo proxy."""

    def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.repo = repo
        headers: dict[str, str] = {}
        if token:
            headers["Authorization"] = f"token {token}"
        self.client = httpx.Client(
            headers=headers,
            trust_env=False,
        )

    def _dit_prefix(self) -> str:
        return f"{self.base_url}/api/v1/repos/{self.repo}"

    def _refs_url(self, ref_type: str, name: str) -> str:
        return f"{self._dit_prefix()}/refs/{ref_type}/{name}"

    def _objects_url(self, obj_type: str, hash_hex: str) -> str:
        return f"{self._dit_prefix()}/objects/{obj_type}/{hash_hex}"

    def create_repo(self, name: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/api/v1/repos", json={"name": name}
        )
        response.raise_for_status()
        return response.json()

    def list_repos(self) -> list[dict]:
        response = self.client.get(f"{self.base_url}/api/v1/repos")
        response.raise_for_status()
        return response.json()

    def get_ref(self, ref_type: str, name: str) -> str | None:
        response = self.client.get(self._refs_url(ref_type, name))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["target_hash"]

    def list_refs(self) -> list[dict]:
        response = self.client.get(f"{self._dit_prefix()}/refs")
        response.raise_for_status()
        return response.json()

    def cas_ref(self, ref_type: str, name: str, old: str | None, new: str) -> bool:
        response = self.client.post(
            self._refs_url(ref_type, name),
            json={"old": old, "new": new},
        )
        if response.status_code == 409:
            return False
        response.raise_for_status()
        return True

    def upload_object(self, obj_type: str, hash_hex: str, data: bytes) -> None:
        response = self.client.post(
            self._objects_url(obj_type, hash_hex),
            content=data,
        )
        response.raise_for_status()

    def upload_batch(self, obj_type: str, items: list[tuple[str, bytes]]) -> dict:
        """Upload multiple objects in a single request.

        items: list of (hash_hex, data) tuples.
        Returns {"accepted": int, "errors": list[str]}
        Falls back to individual uploads if server returns 404 (old server).
        """
        payload = {
            "obj_type": obj_type,
            "items": [
                {"hash": h, "data_b64": base64.b64encode(d).decode("ascii")}
                for h, d in items
            ],
        }
        response = self.client.post(
            f"{self._dit_prefix()}/objects/batch-upload",
            json=payload,
        )
        if response.status_code == 404:
            # Fallback: server doesn't support batch upload
            for h, d in items:
                self.upload_object(obj_type, h, d)
            return {"accepted": len(items), "errors": []}
        response.raise_for_status()
        return response.json()

    def download_object(self, obj_type: str, hash_hex: str) -> bytes | None:
        response = self.client.get(self._objects_url(obj_type, hash_hex))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def download_batch(self, obj_type: str, hashes: list[str]) -> dict[str, bytes]:
        """Download multiple objects in one request. Returns {hash: data}.

        Falls back to individual downloads if server returns 404 (old server).
        """
        resp = self.client.post(
            f"{self._dit_prefix()}/objects/batch-download",
            json={"obj_type": obj_type, "hashes": hashes},
        )
        if resp.status_code == 404:
            # Fallback: server doesn't support batch download
            result: dict[str, bytes] = {}
            for h in hashes:
                data = self.download_object(obj_type, h)
                if data is not None:
                    result[h] = data
            return result
        resp.raise_for_status()
        result = {}
        for item in resp.json()["items"]:
            result[item["hash"]] = base64.b64decode(item["data_b64"])
        return result

    def batch_exists(self, obj_type: str, hashes: list[str]) -> dict[str, bool]:
        response = self.client.post(
            f"{self._dit_prefix()}/objects/batch-exists",
            json={"obj_type": obj_type, "hashes": hashes},
        )
        response.raise_for_status()
        return response.json()["exists"]
