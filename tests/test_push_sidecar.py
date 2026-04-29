"""Verify that push includes sidecars in the correct upload order."""
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.store import ObjectStore
from dit.core.refs import RefStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Sidecar, SidecarEntry, Tree, TreeEntry,
    serialize_commit, serialize_manifest, serialize_sidecar, serialize_tree,
)

runner = CliRunner()


class TestPushUploadOrder:
    def _build_repo_with_sidecar(self, tmp_path: Path):
        """Create a repo dir with one committed manifest + sidecar, return dot path."""
        dot = tmp_path / ".dit"
        dot.mkdir()
        (dot / "objects").mkdir()
        refs = RefStore(dot)
        refs.init()
        store = ObjectStore(dot / "objects")

        manifest = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(manifest))

        sidecar_entries = [
            SidecarEntry(row_hash="a" * 64, char_count=10, token_estimate=2, field_count=1, lang="en")
        ]
        sidecar = Sidecar(manifest_hash=m_hash, entries=sidecar_entries)
        sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

        row_data = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
        store.write("rows", row_data)

        tree = Tree(entries=[TreeEntry(name="train.jsonl", obj_type="manifest", obj_hash=m_hash, sidecar_hash=sc_hash)])
        tree_hash = store.write("trees", serialize_tree(tree))

        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=1000)
        commit_hash = store.write("commits", serialize_commit(commit))

        refs.set_branch("main", commit_hash)
        (dot / "HEAD").write_text("ref:main\n")

        import json as _json
        config = {"remotes": {"origin": {"url": "http://localhost:9999/owner/testrepo", "token": "tok"}}}
        (dot / "config.json").write_text(_json.dumps(config))

        return dot, store, commit_hash, sc_hash

    def test_push_upload_order_includes_sidecars(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, commit_hash, sc_hash = self._build_repo_with_sidecar(tmp_path)

        uploaded_types: list[str] = []

        def fake_batch_exists(obj_type, hashes):
            return {h: False for h in hashes}

        def fake_upload_batch(obj_type, batch):
            uploaded_types.append(obj_type)

        def fake_get_ref(ref_type, name):
            return None

        def fake_cas_ref(ref_type, name, old, new):
            return True

        mock_rc = MagicMock()
        mock_rc.get_ref.side_effect = fake_get_ref
        mock_rc.batch_exists.side_effect = fake_batch_exists
        mock_rc.upload_batch.side_effect = fake_upload_batch
        mock_rc.cas_ref.side_effect = fake_cas_ref

        with patch("dit.cli.main._build_remote_client", return_value=mock_rc):
            result = runner.invoke(app, ["push", "--remote", "origin", "--branch", "main"])

        assert result.exit_code == 0, result.output

        assert "sidecars" in uploaded_types, f"'sidecars' not in upload sequence: {uploaded_types}"

        if "manifests" in uploaded_types and "sidecars" in uploaded_types:
            assert uploaded_types.index("manifests") < uploaded_types.index("sidecars"), \
                "manifests must be uploaded before sidecars"
        if "sidecars" in uploaded_types and "trees" in uploaded_types:
            assert uploaded_types.index("sidecars") < uploaded_types.index("trees"), \
                "sidecars must be uploaded before trees"
        if "trees" in uploaded_types and "commits" in uploaded_types:
            assert uploaded_types.index("trees") < uploaded_types.index("commits"), \
                "trees must be uploaded before commits"

    def test_push_batch_exists_called_for_sidecars(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, commit_hash, sc_hash = self._build_repo_with_sidecar(tmp_path)

        batch_exists_calls: list[str] = []

        def fake_batch_exists(obj_type, hashes):
            batch_exists_calls.append(obj_type)
            return {h: False for h in hashes}

        mock_rc = MagicMock()
        mock_rc.get_ref.return_value = None
        mock_rc.batch_exists.side_effect = fake_batch_exists
        mock_rc.upload_batch.return_value = None
        mock_rc.cas_ref.return_value = True

        with patch("dit.cli.main._build_remote_client", return_value=mock_rc):
            runner.invoke(app, ["push", "--remote", "origin", "--branch", "main"])

        assert "sidecars" in batch_exists_calls, \
            f"batch_exists was not called for sidecars. Calls: {batch_exists_calls}"
