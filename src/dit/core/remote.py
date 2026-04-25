from __future__ import annotations

import httpx


class RemoteClient:
    """Synchronous HTTP client for the Dit server API via Forgejo proxy."""

    def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.repo = repo
        self.client = httpx.Client(
            headers={"Authorization": f"token {token}"},
            trust_env=False,
        )

    def _dit_prefix(self) -> str:
        return f"{self.base_url}/api/v1/repos/{self.repo}/dit"

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

    def download_object(self, obj_type: str, hash_hex: str) -> bytes | None:
        response = self.client.get(self._objects_url(obj_type, hash_hex))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def batch_exists(self, obj_type: str, hashes: list[str]) -> dict[str, bool]:
        response = self.client.post(
            f"{self._dit_prefix()}/objects/batch-exists",
            json={"obj_type": obj_type, "hashes": hashes},
        )
        response.raise_for_status()
        return response.json()["exists"]
