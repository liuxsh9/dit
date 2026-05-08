import time
import json
from pathlib import Path
from httpx import AsyncClient

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
    serialize_sidecar, Sidecar, SidecarEntry,
)
from dit.core.tree_builder import build_nested_tree


async def _create_repo(client: AsyncClient, name: str = "meta-repo"):
    resp = await client.post("/api/v1/repos", json={"name": name})
    assert resp.status_code == 201


async def _build_repo_with_sidecar(client: AsyncClient, tmp_path: Path, repo: str = "meta-repo"):
    """Create a repo with one committed manifest + sidecar. Returns (store, commit_hash, sidecar_hash)."""
    await _create_repo(client, repo)
    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    row_hashes = []
    for content in ["hello world", "foo bar", "test data"]:
        row = json.dumps({"messages": [{"role": "user", "content": content}]})
        row_bytes = row.encode("utf-8")
        rh = store.write("rows", row_bytes)
        row_hashes.append(rh)

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes])
    m_hash = store.write("manifests", serialize_manifest(manifest))

    sidecar_entries = [
        SidecarEntry(row_hash=row_hashes[0], char_count=11, token_estimate=2, field_count=1, lang="en"),
        SidecarEntry(row_hash=row_hashes[1], char_count=7, token_estimate=1, field_count=1, lang="en"),
        SidecarEntry(row_hash=row_hashes[2], char_count=9, token_estimate=2, field_count=1, lang="en"),
    ]
    sidecar = Sidecar(manifest_hash=m_hash, entries=sidecar_entries)
    sidecar_bytes = serialize_sidecar(sidecar)
    sc_hash = store.write("sidecars", sidecar_bytes)

    staged = {"train.jsonl": ("manifest", m_hash, sc_hash)}
    tree_hash = build_nested_tree(store, staged)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
    )

    return store, commit_hash, sc_hash


class TestMetaCompute:
    async def test_compute_all(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client)
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "meta-repo" / "objects")

        row_json = json.dumps({"messages": [{"role": "user", "content": "hi"}]})
        row_bytes = row_json.encode("utf-8")
        rh = store.write("rows", row_bytes)

        manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
        m_hash = store.write("manifests", serialize_manifest(manifest))

        staged = {"train.jsonl": ("manifest", m_hash, None)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(
            tree_hash=tree_hash, parent_hashes=[], author="t",
            message="initial", timestamp=int(time.time()),
        )
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/meta-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
        )

        resp = await client.post("/api/v1/repos/meta-repo/meta/compute", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["commit_hash"] == commit_hash
        assert "sidecars" in data
        assert len(data["sidecars"]) >= 1
        assert data["sidecars"][0]["file"] == "train.jsonl"
        assert data["sidecars"][0]["summary"]["row_count"] == 1

        head_resp = await client.get("/api/v1/repos/meta-repo/refs/heads/main")
        assert head_resp.status_code == 200
        assert head_resp.json()["target_hash"] == commit_hash

    async def test_compute_idempotent(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, sc_hash = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.post("/api/v1/repos/meta-repo/meta/compute", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["commit_hash"] == commit_hash
        assert data["sidecars"] == []

    async def test_compute_repo_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.post("/api/v1/repos/no-such-repo/meta/compute", json={})
        assert resp.status_code == 404

    async def test_compute_no_head(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "empty-repo")
        resp = await client.post("/api/v1/repos/empty-repo/meta/compute", json={})
        assert resp.status_code == 400


class TestMetaGet:
    async def test_get_sidecar(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, sc_hash = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.get(f"/api/v1/repos/meta-repo/meta/{commit_hash}/train.jsonl")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert len(data["entries"]) == 3
        assert data["entries"][0]["char_count"] == 11
        assert data["entries"][0]["lang"] == "en"

    async def test_get_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/meta-repo/meta/{'a' * 64}/train.jsonl")
        assert resp.status_code == 404

    async def test_get_file_no_sidecar(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "nosidecar-repo")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "nosidecar-repo" / "objects")
        manifest = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        mh = store.write("manifests", serialize_manifest(manifest))
        staged = {"train.jsonl": ("manifest", mh, None)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/nosidecar-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
        )

        resp = await client.get(f"/api/v1/repos/nosidecar-repo/meta/{commit_hash}/train.jsonl")
        assert resp.status_code == 404

    async def test_get_path_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/meta-repo/meta/{commit_hash}/nonexistent.jsonl")
        assert resp.status_code == 404


class TestMetaSummary:
    async def test_summary_basic(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash, _ = await _build_repo_with_sidecar(client, tmp_path)

        resp = await client.get(f"/api/v1/repos/meta-repo/meta/{commit_hash}/train.jsonl/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 3
        assert data["char_count"] == 11 + 7 + 9
        assert data["token_estimate"] == 2 + 1 + 2
        assert "avg_fields" in data
        assert "lang_distribution" in data
        assert data["lang_distribution"].get("en", 0) == 3

    async def test_summary_empty_manifest(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "empty-sc-repo")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "empty-sc-repo" / "objects")

        m_hash = store.write("manifests", serialize_manifest(Manifest(entries=[])))
        sidecar = Sidecar(manifest_hash=m_hash, entries=[])
        sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

        staged = {"empty.jsonl": ("manifest", m_hash, sc_hash)}
        tree_hash = build_nested_tree(store, staged)
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/empty-sc-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
        )

        resp = await client.get(f"/api/v1/repos/empty-sc-repo/meta/{commit_hash}/empty.jsonl/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 0
        assert data["char_count"] == 0
        assert data["avg_fields"] == 0.0

    async def test_summary_computes_for_file_without_committed_sidecar(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "nosidecar-summary-repo")
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "nosidecar-summary-repo" / "objects")

        row_json = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
        row_hash = store.write("rows", row_json.encode("utf-8"))
        manifest = Manifest(entries=[ManifestEntry(row_hash=row_hash, query_fingerprint=None)])
        manifest_hash = store.write("manifests", serialize_manifest(manifest))
        tree_hash = build_nested_tree(store, {"train.jsonl": ("manifest", manifest_hash, None)})
        commit = Commit(tree_hash=tree_hash, parent_hashes=[], author="t", message="v1", timestamp=int(time.time()))
        commit_hash = store.write("commits", serialize_commit(commit))
        await client.post(
            "/api/v1/repos/nosidecar-summary-repo/refs/heads/main",
            json={"old": None, "new": commit_hash},
        )

        resp = await client.get(f"/api/v1/repos/nosidecar-summary-repo/meta/{commit_hash}/train.jsonl/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert data["row_count"] == 1
        assert data["char_count"] > 0
        assert data["token_estimate"] > 0


class TestMetaDiff:
    async def _build_two_commits(self, client: AsyncClient, tmp_path: Path, repo: str):
        await _create_repo(client, repo)
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / repo / "objects")

        def _make_sidecar(m_hash: str, rows: list[tuple[str, int]]) -> str:
            entries = [
                SidecarEntry(row_hash=rh, char_count=cc, token_estimate=cc // 4,
                             field_count=1, lang="en")
                for rh, cc in rows
            ]
            sc = Sidecar(manifest_hash=m_hash, entries=entries)
            return store.write("sidecars", serialize_sidecar(sc))

        m1 = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
        mh1 = store.write("manifests", serialize_manifest(m1))
        sc1 = _make_sidecar(mh1, [("a" * 64, 100)])

        m2 = Manifest(entries=[
            ManifestEntry(row_hash="a" * 64, query_fingerprint=None),
            ManifestEntry(row_hash="b" * 64, query_fingerprint=None),
        ])
        mh2 = store.write("manifests", serialize_manifest(m2))
        sc2 = _make_sidecar(mh2, [("a" * 64, 100), ("b" * 64, 200)])

        staged1 = {"train.jsonl": ("manifest", mh1, sc1)}
        staged2 = {"train.jsonl": ("manifest", mh2, sc2)}
        th1 = build_nested_tree(store, staged1)
        th2 = build_nested_tree(store, staged2)

        c1 = Commit(tree_hash=th1, parent_hashes=[], author="t", message="v1", timestamp=1000)
        h1 = store.write("commits", serialize_commit(c1))
        c2 = Commit(tree_hash=th2, parent_hashes=[h1], author="t", message="v2", timestamp=2000)
        h2 = store.write("commits", serialize_commit(c2))

        return store, h1, h2

    async def test_diff_basic(self, client: AsyncClient, tmp_path: Path):
        store, h1, h2 = await self._build_two_commits(client, tmp_path, "diff-meta-repo")

        resp = await client.get(f"/api/v1/repos/diff-meta-repo/meta/diff/{h1}/{h2}")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "train.jsonl"
        assert f["new_stats"]["row_count"] == 2
        assert f["old_stats"]["row_count"] == 1
        assert f["delta"]["row_count"] == 1
        assert f["delta"]["token_estimate"] > 0

    async def test_diff_with_file_filter(self, client: AsyncClient, tmp_path: Path):
        store, h1, h2 = await self._build_two_commits(client, tmp_path, "diff-meta-repo2")

        resp = await client.get(f"/api/v1/repos/diff-meta-repo2/meta/diff/{h1}/{h2}?file=train.jsonl")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1

    async def test_diff_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        await _create_repo(client, "diff-meta-repo3")
        resp = await client.get(f"/api/v1/repos/diff-meta-repo3/meta/diff/{'a' * 64}/{'b' * 64}")
        assert resp.status_code == 404
