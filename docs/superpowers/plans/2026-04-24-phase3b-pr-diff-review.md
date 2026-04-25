# Phase 3B: PR & Diff Review — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement PR lifecycle, enhanced diff API with row content, row-level comments, merge execution flow, and conflict resolution for the Dit review workflow.

**Architecture:** PR state lives in dit-core's PostgreSQL (data_pull_request_meta + pr_comment tables). PR operations delegate to existing merge/diff logic. Comments are stored per-PR with optional row-level anchoring. The merge endpoint orchestrates the full merge-then-update-PR flow.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic (migrations), pytest + pytest-asyncio

**Design Spec:** `docs/superpowers/specs/2026-04-24-phase3-web-ui-design.md` section 6

**Depends on:** Phase 3A complete (nested trees, tree_walker, flatten_tree, enhanced diff_api, service token auth, atomic CAS)

---

## File Structure

```
src/dit/
  server/
    models.py                       # add PullRequestMeta, PrComment models
    alembic/
      versions/
        003_pull_request_meta.py    # NEW: data_pull_request_meta table
        004_pr_comment.py           # NEW: pr_comment table
    routes/
      pulls.py                      # NEW: PR CRUD + merge + conflict resolution
      pr_comments.py                # NEW: Comment CRUD
      diff_api.py                   # EXTEND: per-file summary, row content, pagination, file_path filter
      refs.py                       # EXTEND: PR update on push hook
    app.py                          # register 2 new routers

tests/
  server/
    test_models_pr.py               # NEW: PullRequestMeta + PrComment model tests
    test_routes_pulls.py            # NEW: PR CRUD API tests
    test_routes_pulls_merge.py      # NEW: PR merge execution tests
    test_routes_diff_api_enhanced.py # NEW: enhanced diff (per-file, row content, pagination)
    test_routes_pr_comments.py      # NEW: comment CRUD tests
    test_pr_update_on_push.py       # NEW: auto-update PR stats on ref push
    test_routes_conflict_resolution.py # NEW: conflict resolution tests
```

---

## Task 1: PR Data Model + Alembic Migration

**Files:**
- `src/dit/server/models.py`
- `src/dit/server/alembic/versions/003_pull_request_meta.py` (new)
- `tests/server/test_models_pr.py` (new)

### Steps

- [ ] **1.1** Write tests for the PullRequestMeta model in `tests/server/test_models_pr.py`. These verify that the model can be created, written, and read from the database:

```python
# tests/server/test_models_pr.py
import pytest
from sqlalchemy import select

from dit.server.models import PullRequestMeta, Repo


class TestPullRequestMetaModel:
    async def test_create_pr_meta(self, session):
        """PullRequestMeta can be inserted and read back."""
        repo = Repo(name="pr-test-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="Add new training data",
            author="zhangsan",
            status="open",
            source_ref="heads/feature/new-data",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        session.add(pr)
        await session.commit()

        result = await session.execute(
            select(PullRequestMeta).where(PullRequestMeta.id == pr.id)
        )
        loaded = result.scalar_one()
        assert loaded.title == "Add new training data"
        assert loaded.author == "zhangsan"
        assert loaded.status == "open"
        assert loaded.source_ref == "heads/feature/new-data"
        assert loaded.target_ref == "heads/main"
        assert loaded.base_commit == "a" * 64
        assert loaded.source_commit == "b" * 64
        assert loaded.target_commit == "c" * 64
        assert loaded.merge_commit is None
        assert loaded.is_mergeable is None
        assert loaded.conflict_files is None
        assert loaded.stats_added == 0
        assert loaded.stats_removed == 0
        assert loaded.stats_refreshed == 0

    async def test_pr_meta_defaults(self, session):
        """Default values are correctly applied."""
        repo = Repo(name="pr-defaults-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=2,
            title="Test PR",
            author="tester",
            status="open",
            source_ref="heads/feat",
            target_ref="heads/main",
            base_commit="d" * 64,
            source_commit="e" * 64,
            target_commit="f" * 64,
        )
        session.add(pr)
        await session.commit()
        await session.refresh(pr)

        assert pr.stats_added == 0
        assert pr.stats_removed == 0
        assert pr.stats_refreshed == 0
        assert pr.merge_commit is None
        assert pr.created_at is not None

    async def test_pr_meta_unique_pr_id_per_repo(self, session):
        """pull_request_id must be unique within a repo."""
        repo = Repo(name="pr-unique-repo")
        session.add(repo)
        await session.flush()

        pr1 = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="First",
            author="a",
            status="open",
            source_ref="heads/f1",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        pr2 = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="Duplicate",
            author="b",
            status="open",
            source_ref="heads/f2",
            target_ref="heads/main",
            base_commit="d" * 64,
            source_commit="e" * 64,
            target_commit="f" * 64,
        )
        session.add(pr1)
        await session.flush()
        session.add(pr2)
        with pytest.raises(Exception):
            await session.flush()

    async def test_pr_meta_update_stats(self, session):
        """Stats and merge_commit can be updated after creation."""
        repo = Repo(name="pr-update-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=3,
            title="Update test",
            author="tester",
            status="open",
            source_ref="heads/feat",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        session.add(pr)
        await session.commit()

        pr.stats_added = 42
        pr.stats_removed = 10
        pr.stats_refreshed = 5
        pr.is_mergeable = True
        pr.merge_commit = "d" * 64
        pr.status = "merged"
        await session.commit()
        await session.refresh(pr)

        assert pr.stats_added == 42
        assert pr.stats_removed == 10
        assert pr.stats_refreshed == 5
        assert pr.is_mergeable is True
        assert pr.merge_commit == "d" * 64
        assert pr.status == "merged"
```

- [ ] **1.2** Run to confirm failure (PullRequestMeta does not exist yet):

```bash
uv run pytest tests/server/test_models_pr.py -v
```

Expected: `ImportError` — `PullRequestMeta` not found in `dit.server.models`.

- [ ] **1.3** Add the `PullRequestMeta` model to `src/dit/server/models.py`. Append after the `Webhook` class:

```python
# src/dit/server/models.py — append after Webhook class

class PullRequestMeta(Base):
    __tablename__ = "data_pull_request_meta"
    __table_args__ = (
        sa.UniqueConstraint("repo_id", "pull_request_id", name="uq_pr_repo_prid"),
        {"schema": "dit"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("dit.repos.id"), nullable=False)
    pull_request_id: Mapped[int] = mapped_column(nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    source_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    target_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    base_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    source_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    target_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    merge_commit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    is_mergeable: Mapped[Optional[bool]] = mapped_column(nullable=True)
    conflict_files: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    stats_added: Mapped[int] = mapped_column(default=0)
    stats_removed: Mapped[int] = mapped_column(default=0)
    stats_refreshed: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"PullRequestMeta(id={self.id}, repo_id={self.repo_id}, pr_id={self.pull_request_id}, status={self.status!r})"
```

Also add the required import at the top of models.py — `import sqlalchemy as sa` and ensure `Optional` is imported from `typing`.

- [ ] **1.4** Run the model tests to confirm they pass:

```bash
uv run pytest tests/server/test_models_pr.py -v
```

Expected: 4 passed.

- [ ] **1.5** Create Alembic migration `src/dit/server/alembic/versions/003_pull_request_meta.py`:

```python
# src/dit/server/alembic/versions/003_pull_request_meta.py
"""Add data_pull_request_meta table

Revision ID: 003
Revises: 002
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_pull_request_meta",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("dit.repos.id"), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("author", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("target_ref", sa.String(256), nullable=False),
        sa.Column("base_commit", sa.String(64), nullable=False),
        sa.Column("source_commit", sa.String(64), nullable=False),
        sa.Column("target_commit", sa.String(64), nullable=False),
        sa.Column("merge_commit", sa.String(64), nullable=True),
        sa.Column("is_mergeable", sa.Boolean, nullable=True),
        sa.Column("conflict_files", sa.Text, nullable=True),
        sa.Column("stats_added", sa.Integer, server_default=sa.text("0")),
        sa.Column("stats_removed", sa.Integer, server_default=sa.text("0")),
        sa.Column("stats_refreshed", sa.Integer, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repo_id", "pull_request_id", name="uq_pr_repo_prid"),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("data_pull_request_meta", schema="dit")
```

- [ ] **1.6** Run the full server test suite to verify no regressions:

```bash
uv run pytest tests/server/ -v
```

Expected: all existing tests pass, 4 new tests pass.

- [ ] **1.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/models.py src/dit/server/alembic/versions/003_pull_request_meta.py tests/server/test_models_pr.py && git commit -m "feat: PullRequestMeta model + Alembic migration 003 for data_pull_request_meta table"
```

---

## Task 2: PR CRUD API (Create, List, Get, Update)

**Files:**
- `src/dit/server/routes/pulls.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_pulls.py` (new)

### Steps

- [ ] **2.1** Write `tests/server/test_routes_pulls.py`:

```python
# tests/server/test_routes_pulls.py
import json
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_pr_repo(client, tmp_path):
    """Create a repo with main and feature branches for PR testing."""
    resp = await client.post("/api/v1/repos", json={"name": "pr-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "pr-repo" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
    row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")

    # Base commit (row_a only)
    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test", message="base",
        timestamp=int(time.time()),
    )
    h_base = store.write("commits", serialize_commit(c_base))

    # Main commit (row_a + row_b)
    m_main = Manifest(entries=[row_a, row_b])
    m_main_hash = store.write("manifests", serialize_manifest(m_main))
    tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
    c_main = Commit(
        tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main",
        timestamp=int(time.time()),
    )
    h_main = store.write("commits", serialize_commit(c_main))

    # Feature commit (row_a + row_c)
    m_feat = Manifest(entries=[row_a, row_c])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat",
        timestamp=int(time.time()),
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    # Create refs
    await client.post(
        "/api/v1/repos/pr-repo/refs/heads/main",
        json={"old": None, "new": h_main},
    )
    await client.post(
        "/api/v1/repos/pr-repo/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )
    return store, h_base, h_main, h_feat


class TestCreatePR:
    async def test_create_pr_success(self, client, tmp_path):
        store, h_base, h_main, h_feat = await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Add new training data",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "zhangsan",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "Add new training data"
        assert data["status"] == "open"
        assert data["source_ref"] == "heads/feature"
        assert data["target_ref"] == "heads/main"
        assert data["source_commit"] == h_feat
        assert data["target_commit"] == h_main
        assert data["pull_request_id"] == 1
        assert "stats_added" in data
        assert "stats_removed" in data
        assert "is_mergeable" in data

    async def test_create_pr_branch_not_found(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Bad PR",
                "source_branch": "nonexistent",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 404

    async def test_create_pr_same_branch(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Self PR",
                "source_branch": "main",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 400

    async def test_create_pr_repo_not_found(self, client):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/pulls",
            json={
                "title": "Bad",
                "source_branch": "feat",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 404


class TestListPRs:
    async def test_list_prs_empty(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/pr-repo/pulls")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_prs_with_filter(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Open PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        resp = await client.get("/api/v1/repos/pr-repo/pulls?status=open")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "open"

        resp2 = await client.get("/api/v1/repos/pr-repo/pulls?status=closed")
        assert resp2.status_code == 200
        assert resp2.json() == []


class TestGetPR:
    async def test_get_pr_detail(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Detail PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.get(f"/api/v1/repos/pr-repo/pulls/{pr_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Detail PR"
        assert data["pull_request_id"] == pr_id

    async def test_get_pr_not_found(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/pr-repo/pulls/999")
        assert resp.status_code == 404


class TestUpdatePR:
    async def test_close_pr(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "To Close",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_reopen_pr(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "To Reopen",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "open"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

    async def test_update_merged_pr_fails(self, client, tmp_path):
        """Cannot close/reopen a merged PR."""
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Merged PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        # Merge it first
        merge_resp = await client.post(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}/merge",
            json={"message": "merge it", "author": "tester"},
        )
        assert merge_resp.status_code == 200

        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        assert resp.status_code == 400

    async def test_update_title(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Old Title",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"
```

- [ ] **2.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_pulls.py -v
```

Expected: 404 on all routes since `pulls.py` router does not exist.

- [ ] **2.3** Create `src/dit/server/routes/pulls.py`:

```python
# src/dit/server/routes/pulls.py
from __future__ import annotations

import json as _json
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import PullRequestMeta, Ref, Repo
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["pulls"])


# ---------- helpers ----------

def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


async def _resolve_branch(session: AsyncSession, repo_id: int, branch: str) -> str:
    ref_name = f"heads/{branch}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found")
    return ref.target_hash


async def _next_pr_id(session: AsyncSession, repo_id: int) -> int:
    """Generate the next sequential pull_request_id for a repo."""
    result = await session.execute(
        select(sa_func.coalesce(sa_func.max(PullRequestMeta.pull_request_id), 0))
        .where(PullRequestMeta.repo_id == repo_id)
    )
    current_max = result.scalar()
    return current_max + 1


def _compute_diff_stats(store, source_commit: str, target_commit: str) -> dict:
    """Compute diff stats between two commits. Returns dict with stats fields."""
    from dit.core.objects import deserialize_commit, deserialize_manifest, Manifest
    from dit.core.diff import diff_manifests
    from dit.core.tree_walker import flatten_tree

    source_data = store.read("commits", source_commit)
    target_data = store.read("commits", target_commit)
    if source_data is None or target_data is None:
        return {"stats_added": 0, "stats_removed": 0, "stats_refreshed": 0}

    source_c = deserialize_commit(source_data)
    target_c = deserialize_commit(target_data)

    source_flat = flatten_tree(store, source_c.tree_hash)
    target_flat = flatten_tree(store, target_c.tree_hash)

    source_manifests = {
        p: h for p, (t, h) in source_flat.items() if t == "manifest"
    }
    target_manifests = {
        p: h for p, (t, h) in target_flat.items() if t == "manifest"
    }

    total_added = 0
    total_removed = 0
    total_refreshed = 0

    all_paths = set(source_manifests) | set(target_manifests)
    for path in all_paths:
        s_hash = source_manifests.get(path)
        t_hash = target_manifests.get(path)
        if s_hash == t_hash:
            continue

        s_m = Manifest(entries=[])
        t_m = Manifest(entries=[])

        if s_hash:
            s_data = store.read("manifests", s_hash)
            if s_data:
                s_m = deserialize_manifest(s_data)
        if t_hash:
            t_data = store.read("manifests", t_hash)
            if t_data:
                t_m = deserialize_manifest(t_data)

        # Diff: target is "old" (what we merge into), source is "new" (what we're merging)
        result = diff_manifests(t_m, s_m)
        total_added += len(result.added)
        total_removed += len(result.removed)
        total_refreshed += len(result.refreshed)

    return {
        "stats_added": total_added,
        "stats_removed": total_removed,
        "stats_refreshed": total_refreshed,
    }


def _compute_mergeability(store, target_commit: str, source_commit: str) -> tuple[bool, list[str] | None]:
    """Check if source can be merged into target without conflicts.

    Returns (is_mergeable, conflict_files_or_none).
    """
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge

    base_hash = find_merge_base(store, target_commit, source_commit)
    merge_result = three_way_merge(store, base_hash, target_commit, source_commit)

    if not merge_result.conflicts:
        return True, None

    conflict_files = list({c.file_path for c in merge_result.conflicts})
    return False, conflict_files


def _serialize_pr(pr: PullRequestMeta) -> dict:
    """Serialize a PullRequestMeta to a JSON-safe dict."""
    return {
        "id": pr.id,
        "pull_request_id": pr.pull_request_id,
        "repo_id": pr.repo_id,
        "title": pr.title,
        "author": pr.author,
        "status": pr.status,
        "source_ref": pr.source_ref,
        "target_ref": pr.target_ref,
        "base_commit": pr.base_commit,
        "source_commit": pr.source_commit,
        "target_commit": pr.target_commit,
        "merge_commit": pr.merge_commit,
        "is_mergeable": pr.is_mergeable,
        "conflict_files": _json.loads(pr.conflict_files) if pr.conflict_files else None,
        "stats_added": pr.stats_added,
        "stats_removed": pr.stats_removed,
        "stats_refreshed": pr.stats_refreshed,
        "created_at": pr.created_at.isoformat() if pr.created_at else None,
        "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
    }


# ---------- request/response models ----------

class CreatePRRequest(BaseModel):
    title: str
    source_branch: str
    target_branch: str
    author: str


class UpdatePRRequest(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None


class MergePRRequest(BaseModel):
    message: str
    author: str


class ConflictResolutionRequest(BaseModel):
    resolutions: list[dict]
    message: str
    author: str


# ---------- endpoints ----------

@router.post("/pulls", status_code=201)
async def create_pull_request(
    repo: str,
    body: CreatePRRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Create a new pull request."""
    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    if body.source_branch == body.target_branch:
        raise HTTPException(status_code=400, detail="Source and target branches must differ")

    source_commit = await _resolve_branch(session, r.id, body.source_branch)
    target_commit = await _resolve_branch(session, r.id, body.target_branch)

    # Compute merge base
    from dit.core.merge_base import find_merge_base
    base_commit = find_merge_base(store, target_commit, source_commit)
    if base_commit is None:
        base_commit = ""

    # Compute diff stats
    diff_stats = _compute_diff_stats(store, source_commit, target_commit)

    # Check mergeability
    is_mergeable, conflict_files = _compute_mergeability(store, target_commit, source_commit)

    pr_id = await _next_pr_id(session, r.id)

    pr = PullRequestMeta(
        repo_id=r.id,
        pull_request_id=pr_id,
        title=body.title,
        author=body.author,
        status="open",
        source_ref=f"heads/{body.source_branch}",
        target_ref=f"heads/{body.target_branch}",
        base_commit=base_commit,
        source_commit=source_commit,
        target_commit=target_commit,
        is_mergeable=is_mergeable,
        conflict_files=_json.dumps(conflict_files) if conflict_files else None,
        stats_added=diff_stats["stats_added"],
        stats_removed=diff_stats["stats_removed"],
        stats_refreshed=diff_stats["stats_refreshed"],
    )
    session.add(pr)
    await session.commit()
    await session.refresh(pr)

    return _serialize_pr(pr)


@router.get("/pulls")
async def list_pull_requests(
    repo: str,
    status: Optional[str] = Query(default=None, description="Filter by status: open, closed, merged"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """List pull requests for a repo, optionally filtered by status."""
    r = await _get_repo(repo, session)
    stmt = select(PullRequestMeta).where(PullRequestMeta.repo_id == r.id)
    if status:
        stmt = stmt.where(PullRequestMeta.status == status)
    stmt = stmt.order_by(PullRequestMeta.pull_request_id.desc())
    result = await session.execute(stmt)
    prs = result.scalars().all()
    return [_serialize_pr(pr) for pr in prs]


@router.get("/pulls/{pr_id}")
async def get_pull_request(
    repo: str,
    pr_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Get a single pull request by its sequential ID."""
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")
    return _serialize_pr(pr)


@router.patch("/pulls/{pr_id}")
async def update_pull_request(
    repo: str,
    pr_id: int,
    body: UpdatePRRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Update a pull request (title, close, reopen)."""
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")

    if body.status is not None:
        if pr.status == "merged":
            raise HTTPException(status_code=400, detail="Cannot modify a merged PR")
        if body.status not in ("open", "closed"):
            raise HTTPException(status_code=400, detail="Status must be 'open' or 'closed'")
        pr.status = body.status

    if body.title is not None:
        pr.title = body.title

    await session.commit()
    await session.refresh(pr)
    return _serialize_pr(pr)


@router.post("/pulls/{pr_id}/merge")
async def merge_pull_request(
    repo: str,
    pr_id: int,
    body: MergePRRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Merge a pull request. Delegates to existing three_way_merge logic."""
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge
    from dit.core.objects import (
        Commit, Tree, TreeEntry,
        serialize_commit, serialize_tree,
    )

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")

    if pr.status != "open":
        raise HTTPException(status_code=400, detail=f"PR #{pr_id} is {pr.status}, not open")

    # Re-resolve branches to get current HEAD commits
    source_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.source_ref)
    )
    source_ref = source_ref_result.scalar_one_or_none()
    if source_ref is None:
        raise HTTPException(status_code=404, detail=f"Source branch '{pr.source_ref}' not found")
    source_commit = source_ref.target_hash

    target_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.target_ref)
    )
    target_ref = target_ref_result.scalar_one_or_none()
    if target_ref is None:
        raise HTTPException(status_code=404, detail=f"Target branch '{pr.target_ref}' not found")
    target_commit = target_ref.target_hash

    base_hash = find_merge_base(store, target_commit, source_commit)

    # Fast-forward check
    if base_hash == target_commit:
        # Update target ref to source commit (fast-forward)
        from sqlalchemy import update as sa_update
        stmt = (
            sa_update(Ref)
            .where(
                Ref.repo_id == r.id,
                Ref.name == pr.target_ref,
                Ref.target_hash == target_commit,
            )
            .values(target_hash=source_commit)
            .execution_options(synchronize_session=False)
        )
        res = await session.execute(stmt)
        if res.rowcount == 0:
            raise HTTPException(status_code=409, detail="Target branch was updated concurrently")

        pr.merge_commit = source_commit
        pr.status = "merged"
        pr.source_commit = source_commit
        pr.target_commit = target_commit
        await session.commit()
        await session.refresh(pr)
        return {**_serialize_pr(pr), "fast_forward": True}

    # Three-way merge
    merge_result = three_way_merge(store, base_hash, target_commit, source_commit)

    if merge_result.conflicts:
        # Update PR with conflict info
        conflict_paths = list({c.file_path for c in merge_result.conflicts})
        pr.is_mergeable = False
        pr.conflict_files = _json.dumps(conflict_paths)
        await session.commit()
        await session.refresh(pr)
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Merge conflicts detected",
                "conflicts": [
                    {"file_path": c.file_path, "conflict_type": c.conflict_type}
                    for c in merge_result.conflicts
                ],
            },
        )

    # Create merge commit
    tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[target_commit, source_commit],
        author=body.author,
        message=body.message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    merge_commit_hash = store.write("commits", commit_bytes)

    # Atomic CAS update target branch
    from sqlalchemy import update as sa_update
    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == pr.target_ref,
            Ref.target_hash == target_commit,
        )
        .values(target_hash=merge_commit_hash)
        .execution_options(synchronize_session=False)
    )
    res = await session.execute(stmt)
    if res.rowcount == 0:
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")

    # Update PR meta
    pr.merge_commit = merge_commit_hash
    pr.status = "merged"
    pr.source_commit = source_commit
    pr.target_commit = target_commit
    await session.commit()
    await session.refresh(pr)

    return {**_serialize_pr(pr), "fast_forward": False}
```

- [ ] **2.4** Register the router in `src/dit/server/app.py`. Add after the existing merge router:

```python
# src/dit/server/app.py — add inside create_app()

    from dit.server.routes.pulls import router as pulls_router
    application.include_router(pulls_router)
```

- [ ] **2.5** Run the PR CRUD tests:

```bash
uv run pytest tests/server/test_routes_pulls.py -v
```

Expected: all tests pass (create, list, get, update/close/reopen/title, merge integration).

- [ ] **2.6** Run the full server test suite to check for regressions:

```bash
uv run pytest tests/server/ -v
```

- [ ] **2.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/pulls.py src/dit/server/app.py tests/server/test_routes_pulls.py && git commit -m "feat: PR CRUD API — create, list, get, update, merge pull requests"
```

---

## Task 3: Enhanced Diff API (Per-File Summary, Row Content, Pagination, File Filter)

**Files:**
- `src/dit/server/routes/diff_api.py` (extend from 3A)
- `tests/server/test_routes_diff_api_enhanced.py` (new)

### Steps

- [ ] **3.1** Write `tests/server/test_routes_diff_api_enhanced.py` with tests for the new 3B diff features (per-file summary without rows, row_content with position, pagination, file_path filter, ref-based diff):

```python
# tests/server/test_routes_diff_api_enhanced.py
import json
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree
from dit.core.hash import canonical_json, row_hash


def _make_row(store, content: str) -> ManifestEntry:
    row = {"messages": [{"role": "user", "content": content}]}
    canon = canonical_json(row)
    rh = row_hash(row)
    store.write("rows", canon)
    return ManifestEntry(row_hash=rh, query_fingerprint=None)


def _make_refreshable_row(store, user_content: str, assistant_content: str) -> ManifestEntry:
    row = {"messages": [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": assistant_content},
    ]}
    canon = canonical_json(row)
    rh = row_hash(row)
    store.write("rows", canon)
    # query_fingerprint is based on user messages only
    from dit.core.hash import query_fingerprint
    qfp = query_fingerprint(row)
    return ManifestEntry(row_hash=rh, query_fingerprint=qfp)


async def _setup_enhanced_diff_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "ediff-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "ediff-repo" / "objects")

    # Build two files with changes between old and new commits
    row_a = _make_row(store, "hello")
    row_b = _make_row(store, "world")
    row_c = _make_row(store, "new row")
    row_d = _make_row(store, "extra in file2")
    old_refresh = _make_refreshable_row(store, "sort algo", "old answer")
    new_refresh = _make_refreshable_row(store, "sort algo", "new better answer")

    m1_old = Manifest(entries=[row_a, row_b, old_refresh])
    m1_old_hash = store.write("manifests", serialize_manifest(m1_old))

    m1_new = Manifest(entries=[row_b, row_c, new_refresh])
    m1_new_hash = store.write("manifests", serialize_manifest(m1_new))

    m2_old = Manifest(entries=[row_a])
    m2_old_hash = store.write("manifests", serialize_manifest(m2_old))

    m2_new = Manifest(entries=[row_a, row_d])
    m2_new_hash = store.write("manifests", serialize_manifest(m2_new))

    staged_old = {
        "train/data.jsonl": ("manifest", m1_old_hash),
        "eval/test.jsonl": ("manifest", m2_old_hash),
    }
    staged_new = {
        "train/data.jsonl": ("manifest", m1_new_hash),
        "eval/test.jsonl": ("manifest", m2_new_hash),
    }

    tree_old = build_nested_tree(store, staged_old)
    tree_new = build_nested_tree(store, staged_new)

    c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
    h_old = store.write("commits", serialize_commit(c_old))

    c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
    h_new = store.write("commits", serialize_commit(c_new))

    # Create refs
    await client.post(
        "/api/v1/repos/ediff-repo/refs/heads/main",
        json={"old": None, "new": h_old},
    )
    await client.post(
        "/api/v1/repos/ediff-repo/refs/heads/feature",
        json={"old": None, "new": h_new},
    )

    return store, h_old, h_new, row_a, row_b, row_c, row_d, old_refresh, new_refresh


class TestDiffSummaryOnly:
    async def test_summary_without_rows(self, client, tmp_path):
        """When include_rows=False (default), response has per-file counts but no row content."""
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 2
        for f in data["files"]:
            assert "path" in f
            assert "added" in f
            assert "removed" in f
            assert "refreshed" in f
            # No row content in summary mode
            assert "added_rows" not in f
            assert "removed_rows" not in f

    async def test_summary_has_global_totals(self, client, tmp_path):
        """Response includes summary-level aggregated stats."""
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        data = resp.json()
        assert "summary" in data
        assert data["summary"]["files_changed"] == 2
        assert data["summary"]["rows_added"] >= 1
        assert data["summary"]["rows_removed"] >= 1


class TestDiffPerFileDetail:
    async def test_single_file_filter(self, client, tmp_path):
        """path filter returns only one file's diff."""
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "train/data.jsonl"
        assert "added_rows" in f
        assert "removed_rows" in f
        assert "refreshed_rows" in f

    async def test_row_content_included(self, client, tmp_path):
        """Added/removed rows include content when include_rows=True."""
        store, h_old, h_new, row_a, row_b, row_c, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        added_hashes = {r["row_hash"] for r in f["added_rows"]}
        removed_hashes = {r["row_hash"] for r in f["removed_rows"]}
        assert row_c.row_hash in added_hashes
        assert row_a.row_hash in removed_hashes
        # Verify content is present
        for r in f["added_rows"]:
            if r["row_hash"] == row_c.row_hash:
                assert r["content"] is not None
                assert "messages" in r["content"]

    async def test_position_field_present(self, client, tmp_path):
        """Each row entry has a position field for frontend sorting."""
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        for r in f["added_rows"] + f["removed_rows"]:
            assert "position" in r
            assert isinstance(r["position"], int)

    async def test_refreshed_rows_have_old_new(self, client, tmp_path):
        """Refreshed rows include old_row_hash, new_row_hash, query_fingerprint."""
        store, h_old, h_new, *_, old_refresh, new_refresh = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "train/data.jsonl",
                "include_rows": True,
            },
        )
        data = resp.json()
        f = data["files"][0]
        assert len(f["refreshed_rows"]) >= 1
        ref = f["refreshed_rows"][0]
        assert "old_row_hash" in ref
        assert "new_row_hash" in ref
        assert "query_fingerprint" in ref


class TestDiffPagination:
    async def test_pagination_offset_limit(self, client, tmp_path):
        """offset and limit control which rows are returned."""
        resp = await client.post("/api/v1/repos", json={"name": "ediff-page-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "ediff-page-repo" / "objects")

        # Create 20 added rows
        rows = [_make_row(store, f"row {i}") for i in range(20)]
        m_old = Manifest(entries=[])
        m_new = Manifest(entries=rows)
        m_old_hash = store.write("manifests", serialize_manifest(m_old))
        m_new_hash = store.write("manifests", serialize_manifest(m_new))

        tree_old = build_nested_tree(store, {"data.jsonl": ("manifest", m_old_hash)})
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})

        c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
        h_old = store.write("commits", serialize_commit(c_old))
        c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
        h_new = store.write("commits", serialize_commit(c_new))

        resp = await client.post(
            "/api/v1/repos/ediff-page-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "data.jsonl",
                "include_rows": True,
                "offset": 5,
                "limit": 3,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        f = data["files"][0]
        assert len(f["added_rows"]) == 3
        assert f["added_rows"][0]["position"] == 5

    async def test_has_more_field(self, client, tmp_path):
        """Response indicates when there are more rows to fetch."""
        resp = await client.post("/api/v1/repos", json={"name": "ediff-more-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "ediff-more-repo" / "objects")

        rows = [_make_row(store, f"row {i}") for i in range(10)]
        m_old = Manifest(entries=[])
        m_new = Manifest(entries=rows)
        m_old_hash = store.write("manifests", serialize_manifest(m_old))
        m_new_hash = store.write("manifests", serialize_manifest(m_new))

        tree_old = build_nested_tree(store, {"data.jsonl": ("manifest", m_old_hash)})
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})

        c_old = Commit(tree_hash=tree_old, parent_hashes=[], author="t", message="old", timestamp=1000)
        h_old = store.write("commits", serialize_commit(c_old))
        c_new = Commit(tree_hash=tree_new, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
        h_new = store.write("commits", serialize_commit(c_new))

        resp = await client.post(
            "/api/v1/repos/ediff-more-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "path": "data.jsonl",
                "include_rows": True,
                "offset": 0,
                "limit": 5,
            },
        )
        data = resp.json()
        f = data["files"][0]
        assert f["has_more"] is True
        assert f["total_changes"] == 10


class TestDiffRefBased:
    async def test_diff_by_ref_names(self, client, tmp_path):
        """Diff can accept from_ref and to_ref instead of commit hashes."""
        store, h_old, h_new, *_ = await _setup_enhanced_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/ediff-repo/diff",
            json={"from_ref": "heads/main", "to_ref": "heads/feature"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["files"]) == 2
```

- [ ] **3.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_diff_api_enhanced.py -v
```

Expected: failures because the enhanced features (summary, has_more, total_changes, from_ref/to_ref) are not implemented yet.

- [ ] **3.3** Update `src/dit/server/routes/diff_api.py` to add the enhanced diff features. Replace the entire file with the enhanced version:

```python
# src/dit/server/routes/diff_api.py
from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref, Repo
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["diff"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class DiffRequest(BaseModel):
    old_commit: Optional[str] = None
    new_commit: Optional[str] = None
    from_ref: Optional[str] = None
    to_ref: Optional[str] = None
    path: Optional[str] = None
    include_rows: bool = False
    offset: int = 0
    limit: int = 100


@router.post("/{repo}/diff")
async def diff_commits(
    repo: str,
    body: DiffRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Compute diff between two commits or two refs.

    Accepts either old_commit/new_commit (commit hashes) or from_ref/to_ref (ref names).
    Returns per-file summary. With include_rows=True and a specific path, also returns
    row hashes, content, and position.
    """
    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit, deserialize_manifest, Manifest
    from dit.core.diff import diff_manifests
    from dit.core.tree_walker import flatten_tree

    # Resolve commits from refs if needed
    old_hash = body.old_commit
    new_hash = body.new_commit

    if body.from_ref and not old_hash:
        ref_result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.from_ref)
        )
        ref_obj = ref_result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.from_ref}' not found")
        old_hash = ref_obj.target_hash

    if body.to_ref and not new_hash:
        ref_result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == body.to_ref)
        )
        ref_obj = ref_result.scalar_one_or_none()
        if ref_obj is None:
            raise HTTPException(status_code=404, detail=f"Ref '{body.to_ref}' not found")
        new_hash = ref_obj.target_hash

    if not old_hash or not new_hash:
        raise HTTPException(
            status_code=400,
            detail="Must provide old_commit/new_commit or from_ref/to_ref",
        )

    old_commit_data = store.read("commits", old_hash)
    if old_commit_data is None:
        raise HTTPException(status_code=404, detail=f"Old commit '{old_hash[:8]}' not found")

    new_commit_data = store.read("commits", new_hash)
    if new_commit_data is None:
        raise HTTPException(status_code=404, detail=f"New commit '{new_hash[:8]}' not found")

    old_commit = deserialize_commit(old_commit_data)
    new_commit = deserialize_commit(new_commit_data)

    old_flat = flatten_tree(store, old_commit.tree_hash)
    new_flat = flatten_tree(store, new_commit.tree_hash)

    def manifest_map(flat: dict) -> dict[str, str]:
        return {
            p: h for p, (t, h) in flat.items() if t == "manifest"
        }

    old_manifests = manifest_map(old_flat)
    new_manifests = manifest_map(new_flat)

    all_paths = sorted(set(old_manifests) | set(new_manifests))
    if body.path:
        clean = body.path.strip("/")
        all_paths = [p for p in all_paths if p == clean]

    file_diffs = []
    total_added = 0
    total_removed = 0
    total_refreshed = 0

    for path in all_paths:
        old_m_hash = old_manifests.get(path)
        new_m_hash = new_manifests.get(path)

        if old_m_hash == new_m_hash:
            continue

        old_manifest = Manifest(entries=[])
        new_manifest = Manifest(entries=[])

        if old_m_hash:
            old_m_data = store.read("manifests", old_m_hash)
            if old_m_data:
                old_manifest = deserialize_manifest(old_m_data)

        if new_m_hash:
            new_m_data = store.read("manifests", new_m_hash)
            if new_m_data:
                new_manifest = deserialize_manifest(new_m_data)

        result = diff_manifests(old_manifest, new_manifest)

        total_added += len(result.added)
        total_removed += len(result.removed)
        total_refreshed += len(result.refreshed)

        file_entry: dict = {
            "path": path,
            "added": len(result.added),
            "removed": len(result.removed),
            "refreshed": len(result.refreshed),
            "old_total": len(old_manifest.entries),
            "new_total": len(new_manifest.entries),
        }

        if body.include_rows:
            def _row_entry(rh: str, position: int) -> dict:
                content = None
                raw = store.read("rows", rh)
                if raw is not None:
                    try:
                        content = _json.loads(raw)
                    except Exception:
                        content = None
                return {"row_hash": rh, "position": position, "content": content}

            total_changes_in_file = len(result.added) + len(result.removed) + len(result.refreshed)

            added_page = result.added[body.offset: body.offset + body.limit]
            removed_page = result.removed[body.offset: body.offset + body.limit]
            refreshed_page = result.refreshed[body.offset: body.offset + body.limit]

            file_entry["added_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(added_page)
            ]
            file_entry["removed_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(removed_page)
            ]
            file_entry["refreshed_rows"] = [
                {
                    "old_row_hash": old_rh,
                    "new_row_hash": new_rh,
                    "query_fingerprint": qfp,
                }
                for old_rh, new_rh, qfp in refreshed_page
            ]
            file_entry["total_changes"] = total_changes_in_file
            file_entry["has_more"] = (
                body.offset + body.limit < len(result.added)
                or body.offset + body.limit < len(result.removed)
                or body.offset + body.limit < len(result.refreshed)
            )

        file_diffs.append(file_entry)

    files_changed = len(file_diffs)

    return {
        "old_commit": old_hash,
        "new_commit": new_hash,
        "summary": {
            "files_changed": files_changed,
            "rows_added": total_added,
            "rows_removed": total_removed,
            "rows_refreshed": total_refreshed,
        },
        "files": file_diffs,
    }
```

- [ ] **3.4** Run the enhanced diff tests:

```bash
uv run pytest tests/server/test_routes_diff_api_enhanced.py -v
```

Expected: all tests pass.

- [ ] **3.5** Run existing diff tests to ensure backward compatibility:

```bash
uv run pytest tests/server/test_routes_diff_api.py tests/server/test_routes_diff_api_enhanced.py -v
```

- [ ] **3.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/diff_api.py tests/server/test_routes_diff_api_enhanced.py && git commit -m "feat: enhanced diff API — summary, row content, pagination, file filter, ref-based diff"
```

---

## Task 4: PR Update on Push (Auto-Refresh Stats)

**Files:**
- `src/dit/server/routes/refs.py`
- `tests/server/test_pr_update_on_push.py` (new)

### Steps

- [ ] **4.1** Write `tests/server/test_pr_update_on_push.py`:

```python
# tests/server/test_pr_update_on_push.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_push_pr_repo(client, tmp_path):
    """Create a repo with main + feature branches and an open PR."""
    resp = await client.post("/api/v1/repos", json={"name": "push-pr-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "push-pr-repo" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")

    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test", message="base",
        timestamp=int(time.time()),
    )
    h_base = store.write("commits", serialize_commit(c_base))

    m_feat = Manifest(entries=[row_a, row_b])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat",
        timestamp=int(time.time()),
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post(
        "/api/v1/repos/push-pr-repo/refs/heads/main",
        json={"old": None, "new": h_base},
    )
    await client.post(
        "/api/v1/repos/push-pr-repo/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )

    # Create a PR
    pr_resp = await client.post(
        "/api/v1/repos/push-pr-repo/pulls",
        json={
            "title": "Feature PR",
            "source_branch": "feature",
            "target_branch": "main",
            "author": "tester",
        },
    )
    assert pr_resp.status_code == 201
    pr_data = pr_resp.json()

    return store, h_base, h_feat, pr_data


class TestPRUpdateOnPush:
    async def test_push_to_source_updates_pr_stats(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        old_added = pr_data["stats_added"]

        # Push a new commit to feature branch with more rows
        row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")
        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        m_new = Manifest(entries=[row_a, row_b, row_c])
        m_new_hash = store.write("manifests", serialize_manifest(m_new))
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})
        c_new = Commit(
            tree_hash=tree_new, parent_hashes=[h_feat], author="test",
            message="more rows", timestamp=int(time.time()),
        )
        h_new = store.write("commits", serialize_commit(c_new))

        # CAS update the feature ref
        resp = await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/feature",
            json={"old": h_feat, "new": h_new},
        )
        assert resp.status_code == 200

        # Check that PR stats got updated
        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        assert pr_resp.status_code == 200
        updated = pr_resp.json()
        assert updated["source_commit"] == h_new
        assert updated["stats_added"] >= old_added

    async def test_push_to_unrelated_branch_no_update(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        original_source = pr_data["source_commit"]

        # Create a new branch and push to it
        await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/other",
            json={"old": None, "new": h_base},
        )

        # PR should not be affected
        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        assert pr_resp.json()["source_commit"] == original_source

    async def test_push_to_target_updates_mergeability(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]

        # Push a new commit to main (target branch)
        row_d = ManifestEntry(row_hash="d" * 64, query_fingerprint="q4")
        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        m_main_new = Manifest(entries=[row_a, row_d])
        m_main_hash = store.write("manifests", serialize_manifest(m_main_new))
        tree_main_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
        c_main_new = Commit(
            tree_hash=tree_main_new, parent_hashes=[h_base], author="test",
            message="main update", timestamp=int(time.time()),
        )
        h_main_new = store.write("commits", serialize_commit(c_main_new))

        resp = await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/main",
            json={"old": h_base, "new": h_main_new},
        )
        assert resp.status_code == 200

        # PR should have updated target_commit and re-checked mergeability
        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        updated = pr_resp.json()
        assert updated["target_commit"] == h_main_new
        assert updated["is_mergeable"] is not None
```

- [ ] **4.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_pr_update_on_push.py -v
```

Expected: tests fail because ref updates don't trigger PR stat refresh.

- [ ] **4.3** Add PR update logic to `src/dit/server/routes/refs.py`. After a successful CAS update, query for open PRs whose source_ref or target_ref matches the updated ref, then refresh their stats and mergeability. Add this helper function and call it from `cas_update_ref`:

```python
# src/dit/server/routes/refs.py — add this helper function before cas_update_ref

async def _update_prs_for_ref_change(
    session: AsyncSession,
    request: Request,
    repo_name: str,
    repo_id: int,
    ref_name: str,
    new_hash: str,
):
    """After a ref update, refresh any open PRs that reference this branch."""
    from dit.server.models import PullRequestMeta
    from dit.server.routes.pulls import _compute_diff_stats, _compute_mergeability, _store_for_repo

    store = _store_for_repo(request, repo_name)

    # Find open PRs where source_ref or target_ref matches
    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == repo_id,
            PullRequestMeta.status == "open",
            (
                (PullRequestMeta.source_ref == ref_name)
                | (PullRequestMeta.target_ref == ref_name)
            ),
        )
    )
    prs = result.scalars().all()

    for pr in prs:
        # Resolve current source and target commits
        src_result = await session.execute(
            select(Ref).where(Ref.repo_id == repo_id, Ref.name == pr.source_ref)
        )
        src_ref = src_result.scalar_one_or_none()
        tgt_result = await session.execute(
            select(Ref).where(Ref.repo_id == repo_id, Ref.name == pr.target_ref)
        )
        tgt_ref = tgt_result.scalar_one_or_none()

        if src_ref is None or tgt_ref is None:
            continue

        source_commit = src_ref.target_hash
        target_commit = tgt_ref.target_hash

        # Update PR commits
        pr.source_commit = source_commit
        pr.target_commit = target_commit

        # Recompute merge base
        from dit.core.merge_base import find_merge_base
        base_hash = find_merge_base(store, target_commit, source_commit)
        if base_hash is not None:
            pr.base_commit = base_hash

        # Recompute diff stats
        diff_stats = _compute_diff_stats(store, source_commit, target_commit)
        pr.stats_added = diff_stats["stats_added"]
        pr.stats_removed = diff_stats["stats_removed"]
        pr.stats_refreshed = diff_stats["stats_refreshed"]

        # Recompute mergeability
        is_mergeable, conflict_files = _compute_mergeability(store, target_commit, source_commit)
        pr.is_mergeable = is_mergeable
        import json as _json
        pr.conflict_files = _json.dumps(conflict_files) if conflict_files else None

    if prs:
        await session.commit()
```

Then modify `cas_update_ref` to call this after the successful CAS UPDATE branch (both old=None and CAS update paths). Add the call right before the `return` statement in both branches:

```python
# In the CAS UPDATE branch (else block), before `return`:
        await _update_prs_for_ref_change(
            session, request, repo, r.id, ref_name, body.new,
        )
```

Note: `cas_update_ref` needs a `request: Request` parameter. Add it to the function signature (it's available as a FastAPI dependency):

```python
@router.post("/refs/{ref_type}/{name}")
async def cas_update_ref(
    repo: str,
    ref_type: str,
    name: str,
    body: CASRefRequest,
    request: Request,  # <-- add this
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
```

Also add `from fastapi import ... Request` to the imports if not already present.

- [ ] **4.4** Run the PR-update-on-push tests:

```bash
uv run pytest tests/server/test_pr_update_on_push.py -v
```

Expected: all 3 tests pass.

- [ ] **4.5** Run full server suite to confirm no regressions:

```bash
uv run pytest tests/server/ -v
```

- [ ] **4.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/refs.py tests/server/test_pr_update_on_push.py && git commit -m "feat: auto-update PR stats and mergeability on ref push"
```

---

## Task 5: Row-Level Comment Model + Migration

**Files:**
- `src/dit/server/models.py`
- `src/dit/server/alembic/versions/004_pr_comment.py` (new)
- `tests/server/test_models_pr.py` (extend)

### Steps

- [ ] **5.1** Add tests for the `PrComment` model to `tests/server/test_models_pr.py`:

```python
# tests/server/test_models_pr.py — append new class

from dit.server.models import PrComment


class TestPrCommentModel:
    async def test_create_general_comment(self, session):
        """A general PR comment (no file/row anchor) can be created."""
        repo = Repo(name="comment-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()

        comment = PrComment(
            pull_request_meta_id=pr.id,
            author="reviewer1",
            body="Looks good overall!",
        )
        session.add(comment)
        await session.commit()
        await session.refresh(comment)

        assert comment.id is not None
        assert comment.body == "Looks good overall!"
        assert comment.file_path is None
        assert comment.row_hash is None
        assert comment.field_path is None
        assert comment.change_type is None
        assert comment.created_at is not None

    async def test_create_row_level_comment(self, session):
        """A row-level comment with file_path, row_hash, and field_path."""
        repo = Repo(name="row-comment-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()

        comment = PrComment(
            pull_request_meta_id=pr.id,
            author="reviewer2",
            body="This response has wrong formatting",
            file_path="train/data.jsonl",
            row_hash="d" * 64,
            field_path="messages[1].content",
            change_type="added",
        )
        session.add(comment)
        await session.commit()
        await session.refresh(comment)

        assert comment.file_path == "train/data.jsonl"
        assert comment.row_hash == "d" * 64
        assert comment.field_path == "messages[1].content"
        assert comment.change_type == "added"

    async def test_multiple_comments_on_same_row(self, session):
        """Multiple comments can target the same row."""
        repo = Repo(name="multi-comment-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()

        c1 = PrComment(
            pull_request_meta_id=pr.id, author="r1", body="Comment 1",
            file_path="data.jsonl", row_hash="e" * 64, change_type="added",
        )
        c2 = PrComment(
            pull_request_meta_id=pr.id, author="r2", body="Comment 2",
            file_path="data.jsonl", row_hash="e" * 64, change_type="added",
        )
        session.add_all([c1, c2])
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(
            select(PrComment).where(
                PrComment.pull_request_meta_id == pr.id,
                PrComment.row_hash == "e" * 64,
            )
        )
        comments = result.scalars().all()
        assert len(comments) == 2
```

- [ ] **5.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_models_pr.py::TestPrCommentModel -v
```

Expected: `ImportError` — `PrComment` not found.

- [ ] **5.3** Add the `PrComment` model to `src/dit/server/models.py`. Append after `PullRequestMeta`:

```python
# src/dit/server/models.py — append after PullRequestMeta class

class PrComment(Base):
    __tablename__ = "pr_comment"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_meta_id: Mapped[int] = mapped_column(
        ForeignKey("dit.data_pull_request_meta.id"), nullable=False
    )
    author: Mapped[str] = mapped_column(String(256), nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    file_path: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    row_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    field_path: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    change_type: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"PrComment(id={self.id}, pr_meta_id={self.pull_request_meta_id}, author={self.author!r})"
```

- [ ] **5.4** Run the comment model tests:

```bash
uv run pytest tests/server/test_models_pr.py::TestPrCommentModel -v
```

Expected: 3 passed.

- [ ] **5.5** Create Alembic migration `src/dit/server/alembic/versions/004_pr_comment.py`:

```python
# src/dit/server/alembic/versions/004_pr_comment.py
"""Add pr_comment table

Revision ID: 004
Revises: 003
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pr_comment",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "pull_request_meta_id",
            sa.BigInteger,
            sa.ForeignKey("dit.data_pull_request_meta.id"),
            nullable=False,
        ),
        sa.Column("author", sa.String(256), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("row_hash", sa.String(64), nullable=True),
        sa.Column("field_path", sa.String(256), nullable=True),
        sa.Column("change_type", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="dit",
    )
    op.create_index(
        "ix_pr_comment_pr_meta_id",
        "pr_comment",
        ["pull_request_meta_id"],
        schema="dit",
    )


def downgrade() -> None:
    op.drop_index("ix_pr_comment_pr_meta_id", table_name="pr_comment", schema="dit")
    op.drop_table("pr_comment", schema="dit")
```

- [ ] **5.6** Run all model tests:

```bash
uv run pytest tests/server/test_models_pr.py -v
```

Expected: 7 passed (4 PullRequestMeta + 3 PrComment).

- [ ] **5.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/models.py src/dit/server/alembic/versions/004_pr_comment.py tests/server/test_models_pr.py && git commit -m "feat: PrComment model + Alembic migration 004 for row-level PR comments"
```

---

## Task 6: Comment CRUD API

**Files:**
- `src/dit/server/routes/pr_comments.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_pr_comments.py` (new)

### Steps

- [ ] **6.1** Write `tests/server/test_routes_pr_comments.py`:

```python
# tests/server/test_routes_pr_comments.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_comment_pr(client, tmp_path):
    """Create repo + branches + open PR for comment testing."""
    resp = await client.post("/api/v1/repos", json={"name": "comment-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "comment-repo" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint=None)
    m = Manifest(entries=[row_a])
    m_hash = store.write("manifests", serialize_manifest(m))
    tree_hash = build_nested_tree(store, {"data.jsonl": ("manifest", m_hash)})
    c = Commit(
        tree_hash=tree_hash, parent_hashes=[], author="test", message="init",
        timestamp=int(time.time()),
    )
    h = store.write("commits", serialize_commit(c))

    await client.post(
        "/api/v1/repos/comment-repo/refs/heads/main",
        json={"old": None, "new": h},
    )
    await client.post(
        "/api/v1/repos/comment-repo/refs/heads/feature",
        json={"old": None, "new": h},
    )

    pr_resp = await client.post(
        "/api/v1/repos/comment-repo/pulls",
        json={
            "title": "Comment PR",
            "source_branch": "feature",
            "target_branch": "main",
            "author": "tester",
        },
    )
    assert pr_resp.status_code == 201
    return pr_resp.json()["pull_request_id"]


class TestCreateComment:
    async def test_create_general_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "reviewer", "body": "Looks good!"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["body"] == "Looks good!"
        assert data["author"] == "reviewer"
        assert data["file_path"] is None
        assert data["row_hash"] is None

    async def test_create_row_level_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={
                "author": "reviewer",
                "body": "This row needs work",
                "file_path": "data.jsonl",
                "row_hash": "a" * 64,
                "field_path": "messages[0].content",
                "change_type": "added",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["file_path"] == "data.jsonl"
        assert data["row_hash"] == "a" * 64
        assert data["field_path"] == "messages[0].content"
        assert data["change_type"] == "added"

    async def test_create_comment_pr_not_found(self, client, tmp_path):
        await _setup_comment_pr(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/comment-repo/pulls/999/comments",
            json={"author": "r", "body": "nope"},
        )
        assert resp.status_code == 404


class TestListComments:
    async def test_list_all_comments(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "Comment 1"},
        )
        await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r2", "body": "Comment 2"},
        )
        resp = await client.get(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

    async def test_list_comments_by_file(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "On file", "file_path": "data.jsonl"},
        )
        await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r2", "body": "General"},
        )
        resp = await client.get(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments?file_path=data.jsonl"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["file_path"] == "data.jsonl"


class TestUpdateComment:
    async def test_update_body(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        create_resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "Old body"},
        )
        comment_id = create_resp.json()["id"]
        resp = await client.patch(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments/{comment_id}",
            json={"body": "Updated body"},
        )
        assert resp.status_code == 200
        assert resp.json()["body"] == "Updated body"


class TestDeleteComment:
    async def test_delete_comment(self, client, tmp_path):
        pr_id = await _setup_comment_pr(client, tmp_path)
        create_resp = await client.post(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments",
            json={"author": "r1", "body": "To delete"},
        )
        comment_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments/{comment_id}"
        )
        assert resp.status_code == 200

        # Confirm deleted
        list_resp = await client.get(
            f"/api/v1/repos/comment-repo/pulls/{pr_id}/comments"
        )
        assert len(list_resp.json()) == 0
```

- [ ] **6.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_pr_comments.py -v
```

- [ ] **6.3** Create `src/dit/server/routes/pr_comments.py`:

```python
# src/dit/server/routes/pr_comments.py
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import PrComment, PullRequestMeta, Repo
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["pr-comments"])


# ---------- helpers ----------

async def _get_pr_meta(
    session: AsyncSession, repo_id: int, pr_id: int
) -> PullRequestMeta:
    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == repo_id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")
    return pr


def _serialize_comment(c: PrComment) -> dict:
    return {
        "id": c.id,
        "pull_request_meta_id": c.pull_request_meta_id,
        "author": c.author,
        "body": c.body,
        "file_path": c.file_path,
        "row_hash": c.row_hash,
        "field_path": c.field_path,
        "change_type": c.change_type,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


# ---------- request models ----------

class CreateCommentRequest(BaseModel):
    author: str
    body: str
    file_path: Optional[str] = None
    row_hash: Optional[str] = None
    field_path: Optional[str] = None
    change_type: Optional[str] = None


class UpdateCommentRequest(BaseModel):
    body: Optional[str] = None


# ---------- endpoints ----------

@router.post("/pulls/{pr_id}/comments", status_code=201)
async def create_comment(
    repo: str,
    pr_id: int,
    body: CreateCommentRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Create a comment on a pull request. Optionally anchored to a file/row."""
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)

    comment = PrComment(
        pull_request_meta_id=pr_meta.id,
        author=body.author,
        body=body.body,
        file_path=body.file_path,
        row_hash=body.row_hash,
        field_path=body.field_path,
        change_type=body.change_type,
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return _serialize_comment(comment)


@router.get("/pulls/{pr_id}/comments")
async def list_comments(
    repo: str,
    pr_id: int,
    file_path: Optional[str] = Query(default=None, description="Filter by file path"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """List comments for a pull request, optionally filtered by file path."""
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)

    stmt = (
        select(PrComment)
        .where(PrComment.pull_request_meta_id == pr_meta.id)
        .order_by(PrComment.created_at)
    )
    if file_path:
        stmt = stmt.where(PrComment.file_path == file_path)

    result = await session.execute(stmt)
    comments = result.scalars().all()
    return [_serialize_comment(c) for c in comments]


@router.patch("/pulls/{pr_id}/comments/{comment_id}")
async def update_comment(
    repo: str,
    pr_id: int,
    comment_id: int,
    body: UpdateCommentRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Update a comment's body."""
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)

    result = await session.execute(
        select(PrComment).where(
            PrComment.id == comment_id,
            PrComment.pull_request_meta_id == pr_meta.id,
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail=f"Comment #{comment_id} not found")

    if body.body is not None:
        comment.body = body.body

    await session.commit()
    await session.refresh(comment)
    return _serialize_comment(comment)


@router.delete("/pulls/{pr_id}/comments/{comment_id}")
async def delete_comment(
    repo: str,
    pr_id: int,
    comment_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Delete a comment."""
    r = await _get_repo(repo, session)
    pr_meta = await _get_pr_meta(session, r.id, pr_id)

    result = await session.execute(
        select(PrComment).where(
            PrComment.id == comment_id,
            PrComment.pull_request_meta_id == pr_meta.id,
        )
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise HTTPException(status_code=404, detail=f"Comment #{comment_id} not found")

    await session.delete(comment)
    await session.commit()
    return {"status": "deleted", "id": comment_id}
```

- [ ] **6.4** Register the router in `src/dit/server/app.py`:

```python
# src/dit/server/app.py — add inside create_app()

    from dit.server.routes.pr_comments import router as pr_comments_router
    application.include_router(pr_comments_router)
```

- [ ] **6.5** Run the comment CRUD tests:

```bash
uv run pytest tests/server/test_routes_pr_comments.py -v
```

Expected: all tests pass.

- [ ] **6.6** Run full server test suite:

```bash
uv run pytest tests/server/ -v
```

- [ ] **6.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/pr_comments.py src/dit/server/app.py tests/server/test_routes_pr_comments.py && git commit -m "feat: PR comment CRUD API — create, list, update, delete with row-level anchoring"
```

---

## Task 7: PR Merge Execution Endpoint (Detailed Tests)

**Files:**
- `tests/server/test_routes_pulls_merge.py` (new)

Note: The merge endpoint was implemented in Task 2 (`POST /pulls/{pr_id}/merge`). This task adds thorough tests for edge cases: fast-forward, three-way, conflict detection, concurrent merge, and merged-PR immutability.

### Steps

- [ ] **7.1** Write `tests/server/test_routes_pulls_merge.py`:

```python
# tests/server/test_routes_pulls_merge.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_commit, serialize_manifest, serialize_tree,
    deserialize_commit,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_merge_test_repo(client, tmp_path, diverged=True):
    """Create repo with main + feature branches. If diverged=True, both branch from base."""
    resp = await client.post("/api/v1/repos", json={"name": "merge-test"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "merge-test" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
    row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")

    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test",
        message="base", timestamp=int(time.time()),
    )
    h_base = store.write("commits", serialize_commit(c_base))

    if diverged:
        # Main adds row_b
        m_main = Manifest(entries=[row_a, row_b])
        m_main_hash = store.write("manifests", serialize_manifest(m_main))
        tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
        c_main = Commit(
            tree_hash=tree_main, parent_hashes=[h_base], author="test",
            message="main commit", timestamp=int(time.time()),
        )
        h_main = store.write("commits", serialize_commit(c_main))
    else:
        h_main = h_base

    # Feature adds row_c
    m_feat = Manifest(entries=[row_a, row_c])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test",
        message="feat commit", timestamp=int(time.time()),
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post(
        "/api/v1/repos/merge-test/refs/heads/main",
        json={"old": None, "new": h_main},
    )
    await client.post(
        "/api/v1/repos/merge-test/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )

    # Create PR
    pr_resp = await client.post(
        "/api/v1/repos/merge-test/pulls",
        json={
            "title": "Merge test PR",
            "source_branch": "feature",
            "target_branch": "main",
            "author": "tester",
        },
    )
    assert pr_resp.status_code == 201
    return store, h_base, h_main, h_feat, pr_resp.json()


class TestPRMergeThreeWay:
    async def test_three_way_merge_creates_commit(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(
            client, tmp_path, diverged=True
        )
        pr_id = pr_data["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "Merge feature", "author": "merger"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "merged"
        assert data["merge_commit"] is not None
        assert data["fast_forward"] is False
        assert len(data["merge_commit"]) == 64

        # Verify merge commit has two parents
        merge_data = store.read("commits", data["merge_commit"])
        merge_commit = deserialize_commit(merge_data)
        assert len(merge_commit.parent_hashes) == 2
        assert h_main in merge_commit.parent_hashes
        assert h_feat in merge_commit.parent_hashes

        # Verify target branch ref updated
        ref_resp = await client.get("/api/v1/repos/merge-test/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["merge_commit"]


class TestPRMergeFastForward:
    async def test_fast_forward_merge(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(
            client, tmp_path, diverged=False
        )
        pr_id = pr_data["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "FF merge", "author": "merger"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fast_forward"] is True
        assert data["status"] == "merged"

        # Target branch should now point to feature commit
        ref_resp = await client.get("/api/v1/repos/merge-test/refs/heads/main")
        assert ref_resp.json()["target_hash"] == h_feat


class TestPRMergeConflict:
    async def test_merge_with_conflict(self, client, tmp_path):
        """Conflicting changes trigger 409 and update PR conflict_files."""
        resp = await client.post("/api/v1/repos", json={"name": "conflict-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "conflict-repo" / "objects")

        # Both branches modify the same row in conflicting ways
        row_base = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        m_base = Manifest(entries=[row_base])
        m_base_hash = store.write("manifests", serialize_manifest(m_base))
        tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
        c_base = Commit(
            tree_hash=tree_base, parent_hashes=[], author="test",
            message="base", timestamp=1000,
        )
        h_base = store.write("commits", serialize_commit(c_base))

        # Main: delete data.jsonl entirely, add new file
        row_main = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        m_main = Manifest(entries=[row_main])
        m_main_hash = store.write("manifests", serialize_manifest(m_main))
        tree_main = build_nested_tree(store, {"other.jsonl": ("manifest", m_main_hash)})
        c_main = Commit(
            tree_hash=tree_main, parent_hashes=[h_base], author="test",
            message="main", timestamp=2000,
        )
        h_main = store.write("commits", serialize_commit(c_main))

        # Feature: modify data.jsonl
        row_feat = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")
        m_feat = Manifest(entries=[row_base, row_feat])
        m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
        tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
        c_feat = Commit(
            tree_hash=tree_feat, parent_hashes=[h_base], author="test",
            message="feat", timestamp=2000,
        )
        h_feat = store.write("commits", serialize_commit(c_feat))

        await client.post(
            "/api/v1/repos/conflict-repo/refs/heads/main",
            json={"old": None, "new": h_main},
        )
        await client.post(
            "/api/v1/repos/conflict-repo/refs/heads/feature",
            json={"old": None, "new": h_feat},
        )

        pr_resp = await client.post(
            "/api/v1/repos/conflict-repo/pulls",
            json={
                "title": "Conflict PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = pr_resp.json()["pull_request_id"]

        merge_resp = await client.post(
            f"/api/v1/repos/conflict-repo/pulls/{pr_id}/merge",
            json={"message": "try merge", "author": "merger"},
        )
        assert merge_resp.status_code == 409
        detail = merge_resp.json()["detail"]
        assert "conflicts" in detail or "conflict" in str(detail).lower()


class TestPRMergeEdgeCases:
    async def test_merge_already_merged_pr(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(
            client, tmp_path, diverged=True
        )
        pr_id = pr_data["pull_request_id"]

        # Merge once
        resp1 = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "merge", "author": "merger"},
        )
        assert resp1.status_code == 200

        # Try to merge again
        resp2 = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "merge again", "author": "merger"},
        )
        assert resp2.status_code == 400

    async def test_merge_closed_pr(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(
            client, tmp_path, diverged=True
        )
        pr_id = pr_data["pull_request_id"]

        # Close the PR
        await client.patch(
            f"/api/v1/repos/merge-test/pulls/{pr_id}",
            json={"status": "closed"},
        )

        # Try to merge
        resp = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "merge", "author": "merger"},
        )
        assert resp.status_code == 400
```

- [ ] **7.2** Run the merge tests:

```bash
uv run pytest tests/server/test_routes_pulls_merge.py -v
```

Expected: all tests pass (since merge logic was implemented in Task 2).

- [ ] **7.3** If any tests fail, fix the implementation in `src/dit/server/routes/pulls.py` accordingly.

- [ ] **7.4** Commit:

```bash
cd /Users/lxs/code/dit && git add tests/server/test_routes_pulls_merge.py && git commit -m "test: comprehensive PR merge tests — three-way, fast-forward, conflict, edge cases"
```

---

## Task 8: Conflict Resolution API

**Files:**
- `src/dit/server/routes/pulls.py` (extend)
- `tests/server/test_routes_conflict_resolution.py` (new)

### Steps

- [ ] **8.1** Write `tests/server/test_routes_conflict_resolution.py`:

```python
# tests/server/test_routes_conflict_resolution.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest, deserialize_commit,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_conflict_repo(client, tmp_path):
    """Create a repo with conflicting branches for resolution testing."""
    resp = await client.post("/api/v1/repos", json={"name": "resolve-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "resolve-repo" / "objects")

    # Base: one row with qfp
    row_base = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    m_base = Manifest(entries=[row_base])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test",
        message="base", timestamp=1000,
    )
    h_base = store.write("commits", serialize_commit(c_base))

    # Main: refreshes the row (same qfp, different hash)
    row_main = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
    m_main = Manifest(entries=[row_main])
    m_main_hash = store.write("manifests", serialize_manifest(m_main))
    tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
    c_main = Commit(
        tree_hash=tree_main, parent_hashes=[h_base], author="test",
        message="main refresh", timestamp=2000,
    )
    h_main = store.write("commits", serialize_commit(c_main))

    # Feature: also refreshes the row (same qfp, yet another hash)
    row_feat = ManifestEntry(row_hash="c" * 64, query_fingerprint="q1")
    m_feat = Manifest(entries=[row_feat])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test",
        message="feat refresh", timestamp=2000,
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post(
        "/api/v1/repos/resolve-repo/refs/heads/main",
        json={"old": None, "new": h_main},
    )
    await client.post(
        "/api/v1/repos/resolve-repo/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )

    pr_resp = await client.post(
        "/api/v1/repos/resolve-repo/pulls",
        json={
            "title": "Conflict PR",
            "source_branch": "feature",
            "target_branch": "main",
            "author": "tester",
        },
    )
    assert pr_resp.status_code == 201
    pr_data = pr_resp.json()
    assert pr_data["is_mergeable"] is False

    return store, h_base, h_main, h_feat, pr_data


class TestConflictResolution:
    async def test_resolve_choosing_theirs(self, client, tmp_path):
        """Resolve conflicts by choosing 'theirs' (source/feature) for each row."""
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]

        # First confirm merge fails with conflicts
        merge_resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/merge",
            json={"message": "merge", "author": "merger"},
        )
        assert merge_resp.status_code == 409

        # Resolve the conflicts
        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [
                    {
                        "file_path": "data.jsonl",
                        "row_hash": "c" * 64,
                        "choice": "theirs",
                    },
                ],
                "message": "Resolve: pick feature version",
                "author": "resolver",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "merged"
        assert data["merge_commit"] is not None
        assert len(data["merge_commit"]) == 64

        # Verify the resolution commit exists and target ref updated
        ref_resp = await client.get("/api/v1/repos/resolve-repo/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["merge_commit"]

    async def test_resolve_choosing_ours(self, client, tmp_path):
        """Resolve conflicts by choosing 'ours' (target/main)."""
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [
                    {
                        "file_path": "data.jsonl",
                        "row_hash": "b" * 64,
                        "choice": "ours",
                    },
                ],
                "message": "Resolve: pick main version",
                "author": "resolver",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "merged"

    async def test_resolve_non_conflicting_pr_fails(self, client, tmp_path):
        """Cannot resolve a PR that has no conflicts."""
        resp = await client.post("/api/v1/repos", json={"name": "no-conflict"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "no-conflict" / "objects")

        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint=None)
        m = Manifest(entries=[row_a])
        m_hash = store.write("manifests", serialize_manifest(m))
        tree_hash = build_nested_tree(store, {"data.jsonl": ("manifest", m_hash)})
        c = Commit(
            tree_hash=tree_hash, parent_hashes=[], author="test",
            message="init", timestamp=1000,
        )
        h = store.write("commits", serialize_commit(c))

        await client.post(
            "/api/v1/repos/no-conflict/refs/heads/main",
            json={"old": None, "new": h},
        )
        await client.post(
            "/api/v1/repos/no-conflict/refs/heads/feat",
            json={"old": None, "new": h},
        )

        pr_resp = await client.post(
            "/api/v1/repos/no-conflict/pulls",
            json={
                "title": "Clean PR",
                "source_branch": "feat",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = pr_resp.json()["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/no-conflict/pulls/{pr_id}/resolve",
            json={
                "resolutions": [],
                "message": "resolve nothing",
                "author": "r",
            },
        )
        assert resp.status_code == 400

    async def test_resolve_already_merged_pr_fails(self, client, tmp_path):
        """Cannot resolve a merged PR."""
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]

        # Resolve it once
        await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [
                    {"file_path": "data.jsonl", "row_hash": "c" * 64, "choice": "theirs"},
                ],
                "message": "resolve",
                "author": "r",
            },
        )

        # Try again
        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [],
                "message": "re-resolve",
                "author": "r",
            },
        )
        assert resp.status_code == 400
```

- [ ] **8.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_conflict_resolution.py -v
```

Expected: 404 on `/resolve` endpoint (not yet implemented).

- [ ] **8.3** Add the conflict resolution endpoint to `src/dit/server/routes/pulls.py`. Append after the `merge_pull_request` function:

```python
# src/dit/server/routes/pulls.py — append after merge_pull_request

@router.post("/pulls/{pr_id}/resolve")
async def resolve_conflicts(
    repo: str,
    pr_id: int,
    body: ConflictResolutionRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    """Resolve merge conflicts by specifying per-row choices (ours/theirs).

    After resolving, creates a merge commit with the resolved content and
    marks the PR as merged.
    """
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge
    from dit.core.objects import (
        Commit, Manifest, ManifestEntry, Tree, TreeEntry,
        serialize_commit, serialize_manifest, serialize_tree,
        deserialize_manifest,
    )

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(PullRequestMeta).where(
            PullRequestMeta.repo_id == r.id,
            PullRequestMeta.pull_request_id == pr_id,
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail=f"PR #{pr_id} not found")

    if pr.status != "open":
        raise HTTPException(status_code=400, detail=f"PR #{pr_id} is {pr.status}, not open")

    # Re-resolve current branch HEADs
    source_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.source_ref)
    )
    source_ref = source_ref_result.scalar_one_or_none()
    if source_ref is None:
        raise HTTPException(status_code=404, detail=f"Source branch '{pr.source_ref}' not found")
    source_commit = source_ref.target_hash

    target_ref_result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == pr.target_ref)
    )
    target_ref = target_ref_result.scalar_one_or_none()
    if target_ref is None:
        raise HTTPException(status_code=404, detail=f"Target branch '{pr.target_ref}' not found")
    target_commit = target_ref.target_hash

    base_hash = find_merge_base(store, target_commit, source_commit)
    merge_result = three_way_merge(store, base_hash, target_commit, source_commit)

    if not merge_result.conflicts:
        raise HTTPException(
            status_code=400,
            detail="No conflicts to resolve. Use the merge endpoint instead.",
        )

    # Build resolution map: file_path -> list of chosen row_hashes
    resolution_map: dict[str, list[str]] = {}
    for res in body.resolutions:
        fp = res.get("file_path", "")
        rh = res.get("row_hash", "")
        if fp and rh:
            resolution_map.setdefault(fp, []).append(rh)

    # Apply resolutions: for each conflicting file, build a resolved manifest
    # Start from the merged_tree_entries (non-conflicting files), then add resolved files
    resolved_tree_entries = dict(merge_result.merged_tree_entries)

    for conflict in merge_result.conflicts:
        fp = conflict.file_path
        chosen_hashes = set(resolution_map.get(fp, []))

        # Determine which entries to include based on choices
        resolved_entries: list[ManifestEntry] = []

        # Get the current merged entries for this file if any
        if fp in resolved_tree_entries:
            existing_data = store.read("manifests", resolved_tree_entries[fp])
            if existing_data:
                resolved_entries = list(deserialize_manifest(existing_data).entries)

        # Add chosen rows from ours/theirs conflicts
        if conflict.ours_entries:
            for e in conflict.ours_entries:
                if e.row_hash in chosen_hashes:
                    resolved_entries.append(e)
        if conflict.theirs_entries:
            for e in conflict.theirs_entries:
                if e.row_hash in chosen_hashes:
                    resolved_entries.append(e)

        # If no explicit choice matched, use all resolution hashes
        if not resolved_entries and chosen_hashes:
            # User specified hashes directly — include any matching from ours or theirs
            all_entries = (conflict.ours_entries or []) + (conflict.theirs_entries or [])
            for e in all_entries:
                if e.row_hash in chosen_hashes:
                    resolved_entries.append(e)

        # Write resolved manifest
        if resolved_entries:
            resolved_manifest = Manifest(entries=resolved_entries)
            resolved_bytes = serialize_manifest(resolved_manifest)
            resolved_hash = store.write("manifests", resolved_bytes)
            resolved_tree_entries[fp] = resolved_hash

    # Build merge commit tree
    tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in resolved_tree_entries.items()
    ]
    tree = Tree(entries=tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    merge_commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[target_commit, source_commit],
        author=body.author,
        message=body.message,
        timestamp=int(time.time()),
    )
    merge_commit_bytes = serialize_commit(merge_commit)
    merge_commit_hash = store.write("commits", merge_commit_bytes)

    # Atomic CAS update target branch
    from sqlalchemy import update as sa_update
    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == pr.target_ref,
            Ref.target_hash == target_commit,
        )
        .values(target_hash=merge_commit_hash)
        .execution_options(synchronize_session=False)
    )
    res = await session.execute(stmt)
    if res.rowcount == 0:
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")

    # Update PR meta
    pr.merge_commit = merge_commit_hash
    pr.status = "merged"
    pr.is_mergeable = True
    pr.conflict_files = None
    pr.source_commit = source_commit
    pr.target_commit = target_commit
    await session.commit()
    await session.refresh(pr)

    return _serialize_pr(pr)
```

- [ ] **8.4** Run the conflict resolution tests:

```bash
uv run pytest tests/server/test_routes_conflict_resolution.py -v
```

Expected: all 4 tests pass.

- [ ] **8.5** Run the full server test suite:

```bash
uv run pytest tests/server/ -v
```

- [ ] **8.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/pulls.py tests/server/test_routes_conflict_resolution.py && git commit -m "feat: conflict resolution API — per-row ours/theirs choice with resolved merge commit"
```

---

## Final Verification

- [ ] **9.1** Run the complete test suite:

```bash
uv run pytest tests/ -v 2>&1 | tail -30
```

Expected output ends with something like:
```
========== N passed in X.XXs ==========
```
with zero failures.

- [ ] **9.2** Confirm all new files are tracked:

```bash
cd /Users/lxs/code/dit && git status
```

Expected: `nothing to commit, working tree clean`.

- [ ] **9.3** Confirm the API surface is registered:

```bash
cd /Users/lxs/code/dit && uv run python -c "
from dit.server.app import create_app
app = create_app()
routes = [(r.methods, r.path) for r in app.routes if hasattr(r, 'methods')]
for m, p in sorted(routes, key=lambda x: x[1]):
    print(m, p)
"
```

Expected output includes:
```
{'POST'} /api/v1/repos/{repo}/pulls
{'GET'} /api/v1/repos/{repo}/pulls
{'GET'} /api/v1/repos/{repo}/pulls/{pr_id}
{'PATCH'} /api/v1/repos/{repo}/pulls/{pr_id}
{'POST'} /api/v1/repos/{repo}/pulls/{pr_id}/merge
{'POST'} /api/v1/repos/{repo}/pulls/{pr_id}/resolve
{'POST'} /api/v1/repos/{repo}/pulls/{pr_id}/comments
{'GET'} /api/v1/repos/{repo}/pulls/{pr_id}/comments
{'PATCH'} /api/v1/repos/{repo}/pulls/{pr_id}/comments/{comment_id}
{'DELETE'} /api/v1/repos/{repo}/pulls/{pr_id}/comments/{comment_id}
{'POST'} /api/v1/repos/{repo}/diff
```

- [ ] **9.4** Verify Alembic migration chain is continuous:

```bash
ls -la /Users/lxs/code/dit/src/dit/server/alembic/versions/
```

Expected: `001_initial.py`, `002_webhooks.py`, `003_pull_request_meta.py`, `004_pr_comment.py` — a clean sequential chain.
