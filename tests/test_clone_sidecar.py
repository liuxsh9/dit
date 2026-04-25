"""Verify clone and _fetch_objects_since download sidecar objects."""
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from typer.testing import CliRunner

from dit.cli.main import app, _fetch_objects_since
from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Sidecar, SidecarEntry, Tree, TreeEntry,
    serialize_commit, serialize_manifest, serialize_sidecar, serialize_tree,
    deserialize_commit, deserialize_sidecar, object_hash,
)

runner = CliRunner()


def _build_remote_objects():
    """Return (objects_dict, commit_hash, sidecar_hash).
    objects_dict: obj_type -> hash -> bytes, simulating a remote store.
    """
    objects: dict[str, dict[str, bytes]] = {
        "rows": {}, "manifests": {}, "sidecars": {}, "trees": {}, "commits": {},
    }

    def _write(obj_type: str, data: bytes) -> str:
        h = object_hash(data)
        objects[obj_type][h] = data
        return h

    row_data = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
    row_hash = _write("rows", row_data)

    manifest = Manifest(entries=[ManifestEntry(row_hash=row_hash, query_fingerprint=None)])
    m_hash = _write("manifests", serialize_manifest(manifest))

    sidecar = Sidecar(
        manifest_hash=m_hash,
        entries=[SidecarEntry(row_hash=row_hash, char_count=5, token_estimate=1, field_count=1, lang="en")],
    )
    sc_hash = _write("sidecars", serialize_sidecar(sidecar))

    tree = Tree(entries=[
        TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m_hash, sidecar_hash=sc_hash)
    ])
    tree_hash = _write("trees", serialize_tree(tree))

    commit = Commit(
        tree_hash=tree_hash, parent_hashes=[], author="t",
        message="initial", timestamp=int(time.time()),
    )
    commit_hash = _write("commits", serialize_commit(commit))

    return objects, commit_hash, sc_hash


class TestCloneSidecar:
    def test_clone_downloads_sidecar(self, tmp_path, monkeypatch):
        objects, commit_hash, sc_hash = _build_remote_objects()

        def fake_download(obj_type, hash_hex):
            return objects.get(obj_type, {}).get(hash_hex)

        def fake_get_ref(ref_type, name):
            if name == "main":
                return commit_hash
            return None

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.download_object.side_effect = fake_download

        dest = tmp_path / "cloned"
        with patch("dit.core.remote.RemoteClient", return_value=mock_rc):
            result = runner.invoke(
                app,
                ["clone", "http://fake:9999/owner/repo", str(dest), "--token", "tok"],
            )

        assert result.exit_code == 0, result.output

        local_store = ObjectStore(dest / ".dit" / "objects")
        assert local_store.read("sidecars", sc_hash) is not None, \
            "Sidecar object should have been downloaded during clone"

    def test_clone_sidecar_missing_is_nonfatal(self, tmp_path, monkeypatch):
        objects, commit_hash, sc_hash = _build_remote_objects()

        def fake_download(obj_type, hash_hex):
            if obj_type == "sidecars":
                return None
            return objects.get(obj_type, {}).get(hash_hex)

        def fake_get_ref(ref_type, name):
            if name == "main":
                return commit_hash
            return None

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.download_object.side_effect = fake_download

        dest = tmp_path / "cloned2"
        with patch("dit.core.remote.RemoteClient", return_value=mock_rc):
            result = runner.invoke(
                app,
                ["clone", "http://fake:9999/owner/repo", str(dest), "--token", "tok"],
            )

        assert result.exit_code == 0, result.output
        local_store = ObjectStore(dest / ".dit" / "objects")
        assert local_store.read("sidecars", sc_hash) is None


class TestFetchObjectsSince:
    def test_fetch_downloads_sidecar(self, tmp_path):
        objects, commit_hash, sc_hash = _build_remote_objects()

        local_store = ObjectStore(tmp_path / "objects")

        def fake_download(obj_type, hash_hex):
            return objects.get(obj_type, {}).get(hash_hex)

        mock_rc = MagicMock()
        mock_rc.download_object.side_effect = fake_download

        downloaded, manifest_hashes = _fetch_objects_since(mock_rc, local_store, commit_hash, stop_at=None)

        assert local_store.read("sidecars", sc_hash) is not None, \
            "_fetch_objects_since should download sidecar referenced by tree entry"

    def test_fetch_sidecar_missing_is_nonfatal(self, tmp_path):
        objects, commit_hash, sc_hash = _build_remote_objects()

        local_store = ObjectStore(tmp_path / "objects")

        def fake_download(obj_type, hash_hex):
            if obj_type == "sidecars":
                return None
            return objects.get(obj_type, {}).get(hash_hex)

        mock_rc = MagicMock()
        mock_rc.download_object.side_effect = fake_download

        downloaded, manifest_hashes = _fetch_objects_since(mock_rc, local_store, commit_hash, stop_at=None)
        assert downloaded > 0
