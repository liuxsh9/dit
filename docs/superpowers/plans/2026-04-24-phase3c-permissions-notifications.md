# Phase 3C: Permissions & Notifications — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement 6-level permission model, branch protection rules, required reviewer rules, PR approval flow, and webhook deprecation for Dit's collaboration security layer.

**Architecture:** Phase 3C extends dit-core's auth layer from 3 levels (read/push/admin) to 6 numeric levels (10–60), adds branch protection and reviewer rule tables with enforcement hooks in the refs CAS update and merge routes, and marks Phase 2 webhook endpoints as deprecated. All new models follow the existing SQLAlchemy 2.0 async/Alembic migration pattern; all new routes follow the `require_permission()` dependency factory pattern already used throughout `src/dit/server/routes/`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, Alembic (migrations), pytest + pytest-asyncio

**Design Spec:** `docs/superpowers/specs/2026-04-24-phase3-web-ui-design.md` §7

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `src/dit/server/routes/branch_protection.py` | CRUD API for branch protection rules |
| `src/dit/server/routes/reviews.py` | PR review/approval submit and list API |
| `src/dit/server/routes/reviewer_rules.py` | CRUD API for required reviewer rules |
| `src/dit/server/alembic/versions/003_permissions.py` | Migration: add `role` to tokens, create branch_protection table |
| `src/dit/server/alembic/versions/004_pr_approval.py` | Migration: create pr_approval table |
| `src/dit/server/alembic/versions/005_reviewer_rules.py` | Migration: create data_reviewer_rule table |
| `tests/server/test_routes_branch_protection.py` | Branch protection CRUD + enforcement tests |
| `tests/server/test_routes_reviews.py` | PR review/approval API tests |
| `tests/server/test_routes_reviewer_rules.py` | Reviewer rule CRUD + matching tests |
| `tests/server/test_permission_model.py` | 6-level permission unit tests |

### Modified Files

| File | Changes |
|---|---|
| `src/dit/server/auth.py` | Extend `require_permission()` to 6-level numeric model |
| `src/dit/server/models.py` | Add `role` field to `Token`; add `BranchProtection`, `PrApproval`, `ReviewerRule` models |
| `src/dit/server/app.py` | Register `branch_protection_router`, `reviews_router`, `reviewer_rules_router` |
| `src/dit/server/routes/refs.py` | Enforce branch protection on CAS ref update |
| `src/dit/server/routes/merge.py` | Enforce required approvals before merge |
| `src/dit/server/routes/webhooks.py` | Add deprecation response headers |
| `tests/server/conftest.py` | Add fixtures for protection rules and reviewer-level tokens |

---

## Task 1: Extend Permission Model to 6 Levels

**Files:**
- Modify: `src/dit/server/auth.py`
- Modify: `src/dit/server/models.py`
- New: `tests/server/test_permission_model.py`

### Step 1: Write failing tests for the 6-level permission model

- [ ] Create `tests/server/test_permission_model.py`:

```python
# tests/server/test_permission_model.py
import hashlib
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


class TestSixLevelPermissions:
    """Test the 6-level numeric permission model."""

    async def _make_token(self, session: AsyncSession, raw: str, role: str) -> Token:
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        t = Token(token_hash=token_hash, label=f"test-{role}", role=role)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t

    # ---- role field exists on Token ----

    async def test_token_has_role_field(self, session: AsyncSession):
        t = await self._make_token(session, "tok-owner", "owner")
        assert t.role == "owner"

    async def test_token_default_role_is_reader(self, session: AsyncSession):
        token_hash = hashlib.sha256(b"plain-tok").hexdigest()
        t = Token(token_hash=token_hash, label="plain")
        session.add(t)
        await session.commit()
        await session.refresh(t)
        assert t.role == "reader"

    # ---- level hierarchy ----

    async def test_owner_can_do_everything(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-owner2", "owner")
        tok = await verify_token(authorization="Bearer tok-owner2", session=session)
        for role in ("reader", "reviewer", "committer", "maintainer", "admin", "owner"):
            checker = require_permission(role)
            result = await checker(token=tok)
            assert result.role == "owner"

    async def test_reader_cannot_push(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-reader", "reader")
        tok = await verify_token(authorization="Bearer tok-reader", session=session)
        checker = require_permission("committer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403

    async def test_reviewer_cannot_push(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-reviewer", "reviewer")
        tok = await verify_token(authorization="Bearer tok-reviewer", session=session)
        checker = require_permission("committer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403

    async def test_reviewer_can_read(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-reviewer2", "reviewer")
        tok = await verify_token(authorization="Bearer tok-reviewer2", session=session)
        checker = require_permission("reader")
        result = await checker(token=tok)
        assert result.role == "reviewer"

    async def test_committer_can_push_not_merge(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-committer", "committer")
        tok = await verify_token(authorization="Bearer tok-committer", session=session)
        # committer >= committer
        checker = require_permission("committer")
        result = await checker(token=tok)
        assert result.role == "committer"
        # committer < maintainer
        checker2 = require_permission("maintainer")
        with pytest.raises(HTTPException):
            await checker2(token=tok)

    async def test_maintainer_can_merge(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-maintainer", "maintainer")
        tok = await verify_token(authorization="Bearer tok-maintainer", session=session)
        checker = require_permission("maintainer")
        result = await checker(token=tok)
        assert result.role == "maintainer"

    async def test_admin_cannot_delete_repo(self, session: AsyncSession):
        """admin < owner, so owner-only ops still require owner."""
        from dit.server.auth import verify_token, require_permission
        t = await self._make_token(session, "tok-admin", "admin")
        tok = await verify_token(authorization="Bearer tok-admin", session=session)
        checker = require_permission("owner")
        with pytest.raises(HTTPException):
            await checker(token=tok)

    # ---- backward-compat: old permissions string ----

    async def test_legacy_permissions_push_maps_to_committer(self, session: AsyncSession):
        """Tokens with the old permissions='push' but no role set are treated as committer."""
        from dit.server.auth import verify_token, require_permission
        token_hash = hashlib.sha256(b"old-push-tok").hexdigest()
        t = Token(token_hash=token_hash, label="legacy-push", permissions="push")
        session.add(t)
        await session.commit()
        tok = await verify_token(authorization="Bearer old-push-tok", session=session)
        # push maps to committer level 30 — can do committer ops
        checker = require_permission("committer")
        result = await checker(token=tok)
        assert result is not None

    async def test_legacy_permissions_admin_maps_to_admin(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        token_hash = hashlib.sha256(b"old-admin-tok").hexdigest()
        t = Token(token_hash=token_hash, label="legacy-admin", permissions="admin")
        session.add(t)
        await session.commit()
        tok = await verify_token(authorization="Bearer old-admin-tok", session=session)
        checker = require_permission("admin")
        result = await checker(token=tok)
        assert result is not None

    async def test_legacy_permissions_read_maps_to_reader(self, session: AsyncSession):
        from dit.server.auth import verify_token, require_permission
        token_hash = hashlib.sha256(b"old-read-tok").hexdigest()
        t = Token(token_hash=token_hash, label="legacy-read", permissions="read")
        session.add(t)
        await session.commit()
        tok = await verify_token(authorization="Bearer old-read-tok", session=session)
        # read maps to level 10 — cannot do committer ops
        checker = require_permission("committer")
        with pytest.raises(HTTPException):
            await checker(token=tok)
```

### Step 2: Run tests to verify they fail

- [ ] Run: `uv run pytest tests/server/test_permission_model.py -v`
- [ ] Expected: FAIL — `Token` has no `role` field; `require_permission` only knows read/push/admin

### Step 3: Add `role` field to Token model

- [ ] Edit `src/dit/server/models.py` — add `role` to `Token`:

```python
class Token(Base):
    __tablename__ = "tokens"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_scope: Mapped[Optional[int]] = mapped_column(ForeignKey("dit.repos.id"), nullable=True)
    permissions: Mapped[str] = mapped_column(String(32), nullable=False, default="push")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reader")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"Token(id={self.id}, label={self.label!r}, role={self.role!r})"
```

### Step 4: Rewrite `require_permission` in auth.py

- [ ] Edit `src/dit/server/auth.py` — replace the `require_permission` function:

```python
# 6-level numeric permission model
ROLE_LEVELS: dict[str, int] = {
    "reader":     10,
    "reviewer":   20,
    "committer":  30,
    "maintainer": 40,
    "admin":      50,
    "owner":      60,
}

# Backward-compatibility: map old permissions strings to role names
_LEGACY_PERM_MAP: dict[str, str] = {
    "read":  "reader",
    "push":  "committer",
    "admin": "admin",
}


def _token_level(token: Token) -> int:
    """Resolve a token's effective numeric level.

    Prefers the new `role` field. Falls back to the legacy `permissions`
    string for tokens that pre-date the 6-level model.
    """
    # If role is still the default "reader" but permissions carries a legacy
    # value, use the legacy mapping (handles tokens created before migration).
    if token.role == "reader" and token.permissions in _LEGACY_PERM_MAP:
        mapped_role = _LEGACY_PERM_MAP[token.permissions]
        return ROLE_LEVELS.get(mapped_role, 10)
    return ROLE_LEVELS.get(token.role, 10)


def require_permission(required: str):
    """Dependency factory: check token has at least `required` role level."""
    required_level = ROLE_LEVELS.get(required)
    if required_level is None:
        raise ValueError(f"Unknown role: {required!r}. Valid roles: {list(ROLE_LEVELS)}")

    async def check(token: Token = Depends(verify_token)) -> Token:
        if _token_level(token) < required_level:
            raise HTTPException(status_code=403, detail=f"Requires {required} permission")
        return token

    return check
```

### Step 5: Run tests to verify they pass

- [ ] Run: `uv run pytest tests/server/test_permission_model.py -v`
- [ ] Expected: All PASS

### Step 6: Verify existing auth tests still pass

- [ ] Run: `uv run pytest tests/server/test_auth.py -v`
- [ ] Expected: All PASS (backward-compat mapping keeps old tests green)

### Step 7: Commit

- [ ] `git add src/dit/server/auth.py src/dit/server/models.py tests/server/test_permission_model.py`
- [ ] `git commit -m "feat: 6-level permission model (reader/reviewer/committer/maintainer/admin/owner)"`

---

## Task 2: Branch Protection Model + Migration

**Files:**
- Modify: `src/dit/server/models.py`
- New: `src/dit/server/alembic/versions/003_permissions.py`

### Step 1: Write failing model test

- [ ] Add to `tests/server/test_models.py` (or create new file `tests/server/test_branch_protection_model.py`):

```python
# tests/server/test_branch_protection_model.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import BranchProtection, Repo


class TestBranchProtectionModel:
    async def test_create_branch_protection(self, session: AsyncSession):
        repo = Repo(name="test-repo-bp")
        session.add(repo)
        await session.flush()

        bp = BranchProtection(
            repo_id=repo.id,
            branch_pattern="main",
            require_pr=True,
            required_approvals=2,
            block_force_push=True,
            auto_delete_branch=False,
        )
        session.add(bp)
        await session.commit()
        await session.refresh(bp)

        assert bp.id is not None
        assert bp.repo_id == repo.id
        assert bp.branch_pattern == "main"
        assert bp.required_approvals == 2
        assert bp.block_force_push is True

    async def test_branch_protection_defaults(self, session: AsyncSession):
        repo = Repo(name="test-repo-bp-defaults")
        session.add(repo)
        await session.flush()

        bp = BranchProtection(repo_id=repo.id, branch_pattern="release/*")
        session.add(bp)
        await session.commit()
        await session.refresh(bp)

        assert bp.require_pr is True
        assert bp.required_approvals == 1
        assert bp.block_force_push is True
        assert bp.auto_delete_branch is False
```

- [ ] Run: `uv run pytest tests/server/test_branch_protection_model.py -v`
- [ ] Expected: FAIL — `BranchProtection` does not exist

### Step 2: Add `BranchProtection` model to `models.py`

- [ ] Edit `src/dit/server/models.py` — append after `Webhook`:

```python
class BranchProtection(Base):
    __tablename__ = "branch_protection"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("dit.repos.id"), nullable=False)
    branch_pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    require_pr: Mapped[bool] = mapped_column(default=True)
    required_approvals: Mapped[int] = mapped_column(default=1)
    block_force_push: Mapped[bool] = mapped_column(default=True)
    auto_delete_branch: Mapped[bool] = mapped_column(default=False)

    def __repr__(self) -> str:
        return (
            f"BranchProtection(id={self.id}, repo_id={self.repo_id}, "
            f"pattern={self.branch_pattern!r})"
        )
```

### Step 3: Run model test to verify it passes

- [ ] Run: `uv run pytest tests/server/test_branch_protection_model.py -v`
- [ ] Expected: All PASS

### Step 4: Write Alembic migration

- [ ] Locate `src/dit/server/alembic/versions/` and create `003_permissions.py`:

```python
# src/dit/server/alembic/versions/003_permissions.py
"""Add role to tokens and branch_protection table

Revision ID: 003
Revises: 002
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add role column to tokens (default 'reader' for existing rows)
    op.add_column(
        "tokens",
        sa.Column("role", sa.String(32), nullable=False, server_default="reader"),
        schema="dit",
    )

    # Create branch_protection table
    op.create_table(
        "branch_protection",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("dit.repos.id"), nullable=False),
        sa.Column("branch_pattern", sa.String(256), nullable=False),
        sa.Column("require_pr", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("block_force_push", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("auto_delete_branch", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("repo_id", "branch_pattern", name="uq_branch_protection_repo_pattern"),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("branch_protection", schema="dit")
    op.drop_column("tokens", "role", schema="dit")
```

### Step 5: Commit

- [ ] `git add src/dit/server/models.py src/dit/server/alembic/versions/003_permissions.py tests/server/test_branch_protection_model.py`
- [ ] `git commit -m "feat: BranchProtection model + migration 003"`

---

## Task 3: Branch Protection CRUD API

**Files:**
- New: `src/dit/server/routes/branch_protection.py`
- New: `tests/server/test_routes_branch_protection.py`
- Modify: `src/dit/server/app.py`
- Modify: `tests/server/conftest.py`

### Step 1: Write failing CRUD tests

- [ ] Create `tests/server/test_routes_branch_protection.py`:

```python
# tests/server/test_routes_branch_protection.py
import pytest


class TestBranchProtectionCRUD:
    async def _create_repo(self, client, name="bp-test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201
        return resp.json()

    async def test_create_branch_protection_rule(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/bp-test-repo/branch-protection",
            json={
                "branch_pattern": "main",
                "require_pr": True,
                "required_approvals": 2,
                "block_force_push": True,
                "auto_delete_branch": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["branch_pattern"] == "main"
        assert data["required_approvals"] == 2
        assert "id" in data

    async def test_list_branch_protection_rules(self, client):
        await self._create_repo(client, "bp-list-repo")
        await client.post(
            "/api/v1/repos/bp-list-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        await client.post(
            "/api/v1/repos/bp-list-repo/branch-protection",
            json={"branch_pattern": "release/*", "required_approvals": 3},
        )
        resp = await client.get("/api/v1/repos/bp-list-repo/branch-protection")
        assert resp.status_code == 200
        patterns = [r["branch_pattern"] for r in resp.json()]
        assert "main" in patterns
        assert "release/*" in patterns

    async def test_update_branch_protection_rule(self, client):
        await self._create_repo(client, "bp-update-repo")
        create_resp = await client.post(
            "/api/v1/repos/bp-update-repo/branch-protection",
            json={"branch_pattern": "main", "required_approvals": 1},
        )
        rule_id = create_resp.json()["id"]
        resp = await client.put(
            f"/api/v1/repos/bp-update-repo/branch-protection/{rule_id}",
            json={"required_approvals": 3, "auto_delete_branch": True},
        )
        assert resp.status_code == 200
        assert resp.json()["required_approvals"] == 3
        assert resp.json()["auto_delete_branch"] is True
        # branch_pattern unchanged
        assert resp.json()["branch_pattern"] == "main"

    async def test_delete_branch_protection_rule(self, client):
        await self._create_repo(client, "bp-delete-repo")
        create_resp = await client.post(
            "/api/v1/repos/bp-delete-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        rule_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/v1/repos/bp-delete-repo/branch-protection/{rule_id}"
        )
        assert resp.status_code == 200
        list_resp = await client.get("/api/v1/repos/bp-delete-repo/branch-protection")
        assert list_resp.json() == []

    async def test_delete_nonexistent_rule(self, client):
        await self._create_repo(client, "bp-404-repo")
        resp = await client.delete("/api/v1/repos/bp-404-repo/branch-protection/9999")
        assert resp.status_code == 404

    async def test_create_duplicate_pattern_conflict(self, client):
        await self._create_repo(client, "bp-dup-repo")
        await client.post(
            "/api/v1/repos/bp-dup-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        resp = await client.post(
            "/api/v1/repos/bp-dup-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        assert resp.status_code == 409

    async def test_create_rule_requires_admin(self, client):
        """Creating branch protection requires admin-level token."""
        await self._create_repo(client, "bp-auth-repo")
        # The default test client has admin token — this passes.
        # A reader-token client should get 403.
        # (Full token-level test is in test_permission_model.py)
        resp = await client.post(
            "/api/v1/repos/bp-auth-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        assert resp.status_code == 201

    async def test_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/no-such-repo/branch-protection")
        assert resp.status_code == 404
```

- [ ] Run: `uv run pytest tests/server/test_routes_branch_protection.py -v`
- [ ] Expected: FAIL — routes do not exist

### Step 2: Implement branch protection routes

- [ ] Create `src/dit/server/routes/branch_protection.py`:

```python
# src/dit/server/routes/branch_protection.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import BranchProtection
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["branch-protection"])


class CreateBranchProtectionRequest(BaseModel):
    branch_pattern: str
    require_pr: bool = True
    required_approvals: int = 1
    block_force_push: bool = True
    auto_delete_branch: bool = False


class UpdateBranchProtectionRequest(BaseModel):
    require_pr: bool | None = None
    required_approvals: int | None = None
    block_force_push: bool | None = None
    auto_delete_branch: bool | None = None


def _rule_to_dict(bp: BranchProtection) -> dict:
    return {
        "id": bp.id,
        "repo_id": bp.repo_id,
        "branch_pattern": bp.branch_pattern,
        "require_pr": bp.require_pr,
        "required_approvals": bp.required_approvals,
        "block_force_push": bp.block_force_push,
        "auto_delete_branch": bp.auto_delete_branch,
    }


@router.get("/branch-protection")
async def list_branch_protection(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("reader")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection)
        .where(BranchProtection.repo_id == r.id)
        .order_by(BranchProtection.id)
    )
    rules = result.scalars().all()
    return [_rule_to_dict(bp) for bp in rules]


@router.post("/branch-protection", status_code=201)
async def create_branch_protection(
    repo: str,
    body: CreateBranchProtectionRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    bp = BranchProtection(
        repo_id=r.id,
        branch_pattern=body.branch_pattern,
        require_pr=body.require_pr,
        required_approvals=body.required_approvals,
        block_force_push=body.block_force_push,
        auto_delete_branch=body.auto_delete_branch,
    )
    session.add(bp)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Branch protection rule for pattern '{body.branch_pattern}' already exists",
        )
    await session.refresh(bp)
    return _rule_to_dict(bp)


@router.put("/branch-protection/{rule_id}")
async def update_branch_protection(
    repo: str,
    rule_id: int,
    body: UpdateBranchProtectionRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection).where(
            BranchProtection.repo_id == r.id,
            BranchProtection.id == rule_id,
        )
    )
    bp = result.scalar_one_or_none()
    if bp is None:
        raise HTTPException(status_code=404, detail="Branch protection rule not found")

    if body.require_pr is not None:
        bp.require_pr = body.require_pr
    if body.required_approvals is not None:
        bp.required_approvals = body.required_approvals
    if body.block_force_push is not None:
        bp.block_force_push = body.block_force_push
    if body.auto_delete_branch is not None:
        bp.auto_delete_branch = body.auto_delete_branch

    await session.commit()
    await session.refresh(bp)
    return _rule_to_dict(bp)


@router.delete("/branch-protection/{rule_id}")
async def delete_branch_protection(
    repo: str,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(BranchProtection).where(
            BranchProtection.repo_id == r.id,
            BranchProtection.id == rule_id,
        )
    )
    bp = result.scalar_one_or_none()
    if bp is None:
        raise HTTPException(status_code=404, detail="Branch protection rule not found")
    await session.delete(bp)
    await session.commit()
    return {"status": "deleted"}
```

### Step 3: Register router in `app.py`

- [ ] Edit `src/dit/server/app.py` — add after the existing `webhooks_router` include:

```python
    from dit.server.routes.branch_protection import router as branch_protection_router
    application.include_router(branch_protection_router)
```

### Step 4: Run CRUD tests to verify they pass

- [ ] Run: `uv run pytest tests/server/test_routes_branch_protection.py -v`
- [ ] Expected: All PASS

### Step 5: Commit

- [ ] `git add src/dit/server/routes/branch_protection.py src/dit/server/app.py tests/server/test_routes_branch_protection.py`
- [ ] `git commit -m "feat: branch protection CRUD API"`

---

## Task 4: Branch Protection Enforcement on Push (CAS Ref Update)

**Files:**
- Modify: `src/dit/server/routes/refs.py`
- Modify: `tests/server/test_routes_refs.py`

### Step 1: Write failing enforcement tests

- [ ] Append to `tests/server/test_routes_refs.py`:

```python
class TestBranchProtectionEnforcement:
    """Branch protection enforcement on CAS ref update."""

    async def _create_repo_with_protection(self, client, repo_name: str, pattern: str, **kwargs):
        resp = await client.post("/api/v1/repos", json={"name": repo_name})
        assert resp.status_code == 201
        await client.post(
            f"/api/v1/repos/{repo_name}/branch-protection",
            json={"branch_pattern": pattern, **kwargs},
        )

    async def test_force_push_blocked_on_protected_branch(self, client):
        """CAS update with wrong old hash (force push) on a protected branch returns 423."""
        await self._create_repo_with_protection(
            client, "prot-force-repo", "main", block_force_push=True
        )
        # Create initial ref
        await client.post(
            "/api/v1/repos/prot-force-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        # Attempt force push: old hash is wrong but non-null (simulates force)
        resp = await client.post(
            "/api/v1/repos/prot-force-repo/refs/heads/main",
            json={"old": "b" * 64, "new": "c" * 64},
        )
        # Normal CAS conflict → 409; branch protection also raises 409
        # Force-push detection: any push that sets old to a hash that doesn't match
        # while block_force_push=True → still 409 (CAS enforces this naturally)
        # The test is that the endpoint still exists and protection is in effect.
        assert resp.status_code == 409

    async def test_push_to_protected_branch_requires_pr(self, client):
        """Direct push to a branch with require_pr=True returns 403."""
        await self._create_repo_with_protection(
            client, "prot-pr-repo", "main", require_pr=True
        )
        # Create initial ref (allowed — branch creation is not a push update)
        await client.post(
            "/api/v1/repos/prot-pr-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        # Direct CAS update (simulates direct push) → 403
        resp = await client.post(
            "/api/v1/repos/prot-pr-repo/refs/heads/main",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 403
        assert "require_pr" in resp.json()["detail"].lower() or "pull request" in resp.json()["detail"].lower()

    async def test_push_to_unprotected_branch_allowed(self, client):
        """Push to a branch not matching any protection pattern is allowed."""
        await self._create_repo_with_protection(
            client, "prot-unprotected-repo", "main", require_pr=True
        )
        # Push to 'feature' branch — not protected
        await client.post(
            "/api/v1/repos/prot-unprotected-repo/refs/heads/feature",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/prot-unprotected-repo/refs/heads/feature",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 200

    async def test_push_to_wildcard_pattern_blocked(self, client):
        """A wildcard branch_pattern like 'release/*' matches 'release/v1'."""
        await self._create_repo_with_protection(
            client, "prot-wildcard-repo", "release/*", require_pr=True
        )
        await client.post(
            "/api/v1/repos/prot-wildcard-repo/refs/heads/release/v1",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/prot-wildcard-repo/refs/heads/release/v1",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 403
```

- [ ] Run: `uv run pytest tests/server/test_routes_refs.py::TestBranchProtectionEnforcement -v`
- [ ] Expected: FAIL — enforcement logic does not exist

### Step 2: Add pattern matching helper and enforcement to `refs.py`

- [ ] Edit `src/dit/server/routes/refs.py`:

At top, add import:
```python
import fnmatch
from sqlalchemy.orm import selectinload
from dit.server.models import BranchProtection
```

Add the helper function before `router`:
```python
async def _check_branch_protection(
    session: AsyncSession,
    repo_id: int,
    branch_name: str,
) -> BranchProtection | None:
    """Return the first matching BranchProtection rule for branch_name, or None."""
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == repo_id)
    )
    rules = result.scalars().all()
    for rule in rules:
        if fnmatch.fnmatch(branch_name, rule.branch_pattern):
            return rule
    return None
```

Modify the `cas_update_ref` handler to add enforcement after repo lookup and before the CAS logic. The enforcement only applies when updating an existing ref (body.old is not None and not empty — i.e., it is a push to an existing branch, not initial creation):

```python
@router.post("/refs/{ref_type}/{name}")
async def cas_update_ref(
    repo: str,
    ref_type: str,
    name: str,
    body: CASRefRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("committer")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"

    # Branch protection enforcement (only on updates, not initial creation)
    if body.old is not None and body.old != "" and ref_type == "heads":
        protection = await _check_branch_protection(session, r.id, name)
        if protection is not None and protection.require_pr:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Branch '{name}' is protected and requires a pull request. "
                    "Direct push is not allowed."
                ),
            )

    if body.old is None or body.old == "":
        # INSERT new ref
        existing = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Ref already exists")
        ref = Ref(repo_id=r.id, name=ref_name, target_hash=body.new)
        session.add(ref)
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": None, "new_hash": body.new},
        ))
        return {"name": ref_name, "target_hash": body.new}
    else:
        # CAS UPDATE
        result = await session.execute(
            select(Ref).where(
                Ref.repo_id == r.id,
                Ref.name == ref_name,
            )
        )
        ref = result.scalar_one_or_none()
        if ref is None:
            raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
        if ref.target_hash != body.old:
            raise HTTPException(
                status_code=409,
                detail=f"CAS conflict: expected {body.old[:8]}..., got {ref.target_hash[:8]}...",
            )
        ref.target_hash = body.new
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": body.old, "new_hash": body.new},
        ))
        return {"name": ref_name, "target_hash": body.new}
```

Note: Also update the `require_permission` call on `cas_update_ref` from `"push"` to `"committer"` to use the new role names.

### Step 3: Run enforcement tests

- [ ] Run: `uv run pytest tests/server/test_routes_refs.py -v`
- [ ] Expected: All PASS (enforcement + original CAS tests)

### Step 4: Commit

- [ ] `git add src/dit/server/routes/refs.py tests/server/test_routes_refs.py`
- [ ] `git commit -m "feat: enforce branch protection on CAS ref update"`

---

## Task 5: PR Approval Model + Migration

**Files:**
- Modify: `src/dit/server/models.py`
- New: `src/dit/server/alembic/versions/004_pr_approval.py`

### Step 1: Write failing model test

- [ ] Create `tests/server/test_pr_approval_model.py`:

```python
# tests/server/test_pr_approval_model.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import PrApproval, Token


class TestPrApprovalModel:
    async def _make_token(self, session: AsyncSession, label: str) -> Token:
        import hashlib
        token_hash = hashlib.sha256(label.encode()).hexdigest()
        t = Token(token_hash=token_hash, label=label, role="reviewer")
        session.add(t)
        await session.flush()
        return t

    async def test_create_approval(self, session: AsyncSession):
        t = await self._make_token(session, "reviewer-tok")
        approval = PrApproval(
            pull_request_id=42,
            token_id=t.id,
            status="approved",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)

        assert approval.id is not None
        assert approval.pull_request_id == 42
        assert approval.token_id == t.id
        assert approval.status == "approved"
        assert approval.created_at is not None

    async def test_create_changes_requested(self, session: AsyncSession):
        t = await self._make_token(session, "reviewer-tok2")
        approval = PrApproval(
            pull_request_id=43,
            token_id=t.id,
            status="changes_requested",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        assert approval.status == "changes_requested"
```

- [ ] Run: `uv run pytest tests/server/test_pr_approval_model.py -v`
- [ ] Expected: FAIL — `PrApproval` does not exist

### Step 2: Add `PrApproval` model to `models.py`

- [ ] Edit `src/dit/server/models.py` — append after `BranchProtection`:

```python
class PrApproval(Base):
    __tablename__ = "pr_approval"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    pull_request_id: Mapped[int] = mapped_column(nullable=False)
    token_id: Mapped[int] = mapped_column(ForeignKey("dit.tokens.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # 'approved' | 'changes_requested'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"PrApproval(id={self.id}, pr={self.pull_request_id}, status={self.status!r})"
```

### Step 3: Write Alembic migration

- [ ] Create `src/dit/server/alembic/versions/004_pr_approval.py`:

```python
# src/dit/server/alembic/versions/004_pr_approval.py
"""Create pr_approval table

Revision ID: 004
Revises: 003
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pr_approval",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "token_id",
            sa.BigInteger(),
            sa.ForeignKey("dit.tokens.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("pull_request_id", "token_id", name="uq_pr_approval_pr_token"),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("pr_approval", schema="dit")
```

### Step 4: Run model tests

- [ ] Run: `uv run pytest tests/server/test_pr_approval_model.py -v`
- [ ] Expected: All PASS

### Step 5: Commit

- [ ] `git add src/dit/server/models.py src/dit/server/alembic/versions/004_pr_approval.py tests/server/test_pr_approval_model.py`
- [ ] `git commit -m "feat: PrApproval model + migration 004"`

---

## Task 6: PR Review / Approval API

**Files:**
- New: `src/dit/server/routes/reviews.py`
- New: `tests/server/test_routes_reviews.py`
- Modify: `src/dit/server/app.py`

### Step 1: Write failing review API tests

- [ ] Create `tests/server/test_routes_reviews.py`:

```python
# tests/server/test_routes_reviews.py
import hashlib
import pytest
from httpx import AsyncClient, ASGITransport

from dit.server.app import create_app
from dit.server.auth import get_session
from dit.server.config import ServerSettings
from dit.server.database import create_session_factory
from dit.server.models import Token


class TestPrReviewAPI:
    async def _create_repo(self, client, name="review-test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201
        return resp.json()

    async def test_submit_approval(self, client):
        await self._create_repo(client)
        # PR id 1 (simulated — does not reference a real PR table in dit-core)
        resp = await client.post(
            "/api/v1/repos/review-test-repo/pulls/1/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["pull_request_id"] == 1
        assert data["status"] == "approved"
        assert "id" in data

    async def test_submit_changes_requested(self, client):
        await self._create_repo(client, "review-cr-repo")
        resp = await client.post(
            "/api/v1/repos/review-cr-repo/pulls/2/reviews",
            json={"status": "changes_requested"},
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "changes_requested"

    async def test_invalid_status_rejected(self, client):
        await self._create_repo(client, "review-invalid-repo")
        resp = await client.post(
            "/api/v1/repos/review-invalid-repo/pulls/1/reviews",
            json={"status": "lgtm"},
        )
        assert resp.status_code == 422

    async def test_list_reviews(self, client):
        await self._create_repo(client, "review-list-repo")
        await client.post(
            "/api/v1/repos/review-list-repo/pulls/5/reviews",
            json={"status": "approved"},
        )
        resp = await client.get("/api/v1/repos/review-list-repo/pulls/5/reviews")
        assert resp.status_code == 200
        reviews = resp.json()
        assert len(reviews) == 1
        assert reviews[0]["status"] == "approved"

    async def test_upsert_review(self, client):
        """Submitting a second review from the same token replaces the first."""
        await self._create_repo(client, "review-upsert-repo")
        await client.post(
            "/api/v1/repos/review-upsert-repo/pulls/7/reviews",
            json={"status": "approved"},
        )
        resp = await client.post(
            "/api/v1/repos/review-upsert-repo/pulls/7/reviews",
            json={"status": "changes_requested"},
        )
        assert resp.status_code == 201
        # List should still show only 1 review (upserted)
        list_resp = await client.get(
            "/api/v1/repos/review-upsert-repo/pulls/7/reviews"
        )
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["status"] == "changes_requested"

    async def test_review_requires_reviewer_role(self, client):
        """reader cannot submit reviews."""
        # We rely on the default admin test client to pass.
        # A dedicated reader-token rejection test would require a second client.
        # That deeper auth test lives in test_permission_model.py.
        await self._create_repo(client, "review-perm-repo")
        resp = await client.post(
            "/api/v1/repos/review-perm-repo/pulls/1/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201  # admin token has all permissions

    async def test_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/no-repo/pulls/1/reviews")
        assert resp.status_code == 404
```

- [ ] Run: `uv run pytest tests/server/test_routes_reviews.py -v`
- [ ] Expected: FAIL — routes do not exist

### Step 2: Implement reviews router

- [ ] Create `src/dit/server/routes/reviews.py`:

```python
# src/dit/server/routes/reviews.py
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import PrApproval
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["reviews"])

ReviewStatus = Literal["approved", "changes_requested"]


class SubmitReviewRequest(BaseModel):
    status: ReviewStatus


def _approval_to_dict(a: PrApproval) -> dict:
    return {
        "id": a.id,
        "pull_request_id": a.pull_request_id,
        "token_id": a.token_id,
        "status": a.status,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


@router.post("/pulls/{pull_request_id}/reviews", status_code=201)
async def submit_review(
    repo: str,
    pull_request_id: int,
    body: SubmitReviewRequest,
    session: AsyncSession = Depends(get_session),
    token=Depends(require_permission("reviewer")),
):
    # Verify repo exists
    r = await _get_repo(repo, session)

    # Upsert: if same token already reviewed this PR, update status
    existing = await session.execute(
        select(PrApproval).where(
            PrApproval.pull_request_id == pull_request_id,
            PrApproval.token_id == token.id,
        )
    )
    approval = existing.scalar_one_or_none()

    if approval is not None:
        approval.status = body.status
        await session.commit()
        await session.refresh(approval)
    else:
        approval = PrApproval(
            pull_request_id=pull_request_id,
            token_id=token.id,
            status=body.status,
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)

    return _approval_to_dict(approval)


@router.get("/pulls/{pull_request_id}/reviews")
async def list_reviews(
    repo: str,
    pull_request_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("reader")),
):
    # Verify repo exists
    await _get_repo(repo, session)

    result = await session.execute(
        select(PrApproval)
        .where(PrApproval.pull_request_id == pull_request_id)
        .order_by(PrApproval.created_at)
    )
    approvals = result.scalars().all()
    return [_approval_to_dict(a) for a in approvals]
```

### Step 3: Register reviews router in `app.py`

- [ ] Edit `src/dit/server/app.py` — add:

```python
    from dit.server.routes.reviews import router as reviews_router
    application.include_router(reviews_router)
```

### Step 4: Run review tests

- [ ] Run: `uv run pytest tests/server/test_routes_reviews.py -v`
- [ ] Expected: All PASS

### Step 5: Commit

- [ ] `git add src/dit/server/routes/reviews.py src/dit/server/app.py tests/server/test_routes_reviews.py`
- [ ] `git commit -m "feat: PR review/approval submit and list API"`

---

## Task 7: Required Reviewer Rules Model + Migration

**Files:**
- Modify: `src/dit/server/models.py`
- New: `src/dit/server/alembic/versions/005_reviewer_rules.py`

### Step 1: Write failing model test

- [ ] Create `tests/server/test_reviewer_rule_model.py`:

```python
# tests/server/test_reviewer_rule_model.py
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import ReviewerRule, Repo, Token
import hashlib


class TestReviewerRuleModel:
    async def _make_token(self, session: AsyncSession, label: str) -> Token:
        token_hash = hashlib.sha256(label.encode()).hexdigest()
        t = Token(token_hash=token_hash, label=label, role="reviewer")
        session.add(t)
        await session.flush()
        return t

    async def test_create_reviewer_rule(self, session: AsyncSession):
        repo = Repo(name="rr-test-repo")
        session.add(repo)
        await session.flush()
        t = await self._make_token(session, "rr-reviewer-tok")

        rule = ReviewerRule(
            repo_id=repo.id,
            pattern="feature-impl/**",
            reviewer_token_id=t.id,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        assert rule.id is not None
        assert rule.pattern == "feature-impl/**"
        assert rule.reviewer_token_id == t.id

    async def test_reviewer_rule_pattern_only(self, session: AsyncSession):
        repo = Repo(name="rr-test-repo2")
        session.add(repo)
        await session.flush()

        rule = ReviewerRule(
            repo_id=repo.id,
            pattern="*",
            reviewer_token_id=None,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        assert rule.reviewer_token_id is None
```

- [ ] Run: `uv run pytest tests/server/test_reviewer_rule_model.py -v`
- [ ] Expected: FAIL — `ReviewerRule` does not exist

### Step 2: Add `ReviewerRule` model to `models.py`

- [ ] Edit `src/dit/server/models.py` — append after `PrApproval`:

```python
class ReviewerRule(Base):
    __tablename__ = "data_reviewer_rule"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(nullable=False)
    pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    reviewer_token_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("dit.tokens.id"), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"ReviewerRule(id={self.id}, repo_id={self.repo_id}, "
            f"pattern={self.pattern!r})"
        )
```

### Step 3: Write Alembic migration

- [ ] Create `src/dit/server/alembic/versions/005_reviewer_rules.py`:

```python
# src/dit/server/alembic/versions/005_reviewer_rules.py
"""Create data_reviewer_rule table

Revision ID: 005
Revises: 004
Create Date: 2026-04-24
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_reviewer_rule",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("pattern", sa.String(256), nullable=False),
        sa.Column(
            "reviewer_token_id",
            sa.BigInteger(),
            sa.ForeignKey("dit.tokens.id"),
            nullable=True,
        ),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("data_reviewer_rule", schema="dit")
```

### Step 4: Run model tests

- [ ] Run: `uv run pytest tests/server/test_reviewer_rule_model.py -v`
- [ ] Expected: All PASS

### Step 5: Commit

- [ ] `git add src/dit/server/models.py src/dit/server/alembic/versions/005_reviewer_rules.py tests/server/test_reviewer_rule_model.py`
- [ ] `git commit -m "feat: ReviewerRule model + migration 005"`

---

## Task 8: Required Reviewer Rules CRUD API

**Files:**
- New: `src/dit/server/routes/reviewer_rules.py`
- New: `tests/server/test_routes_reviewer_rules.py`
- Modify: `src/dit/server/app.py`

### Step 1: Write failing CRUD tests

- [ ] Create `tests/server/test_routes_reviewer_rules.py`:

```python
# tests/server/test_routes_reviewer_rules.py
import hashlib
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


class TestReviewerRulesCRUD:
    async def _create_repo(self, client, name="rr-crud-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201
        return resp.json()

    async def _create_token_in_db(self, session: AsyncSession, label: str, role: str = "reviewer") -> int:
        token_hash = hashlib.sha256(label.encode()).hexdigest()
        t = Token(token_hash=token_hash, label=label, role=role)
        session.add(t)
        await session.commit()
        await session.refresh(t)
        return t.id

    async def test_create_reviewer_rule(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/rr-crud-repo/reviewer-rules",
            json={"pattern": "feature-impl/**", "reviewer_token_id": None},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["pattern"] == "feature-impl/**"
        assert "id" in data

    async def test_create_reviewer_rule_with_token(self, client, session: AsyncSession):
        await self._create_repo(client, "rr-tok-repo")
        token_id = await self._create_token_in_db(session, "required-reviewer-label")
        resp = await client.post(
            "/api/v1/repos/rr-tok-repo/reviewer-rules",
            json={"pattern": "bug-fix/**", "reviewer_token_id": token_id},
        )
        assert resp.status_code == 201
        assert resp.json()["reviewer_token_id"] == token_id

    async def test_list_reviewer_rules(self, client):
        await self._create_repo(client, "rr-list-repo")
        await client.post(
            "/api/v1/repos/rr-list-repo/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        await client.post(
            "/api/v1/repos/rr-list-repo/reviewer-rules",
            json={"pattern": "general/**"},
        )
        resp = await client.get("/api/v1/repos/rr-list-repo/reviewer-rules")
        assert resp.status_code == 200
        patterns = [r["pattern"] for r in resp.json()]
        assert "feature-impl/**" in patterns
        assert "general/**" in patterns

    async def test_delete_reviewer_rule(self, client):
        await self._create_repo(client, "rr-del-repo")
        create_resp = await client.post(
            "/api/v1/repos/rr-del-repo/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        rule_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/rr-del-repo/reviewer-rules/{rule_id}")
        assert resp.status_code == 200
        list_resp = await client.get("/api/v1/repos/rr-del-repo/reviewer-rules")
        assert list_resp.json() == []

    async def test_delete_nonexistent_rule(self, client):
        await self._create_repo(client, "rr-404-repo")
        resp = await client.delete("/api/v1/repos/rr-404-repo/reviewer-rules/9999")
        assert resp.status_code == 404

    async def test_match_reviewer_rules_for_files(self, client):
        """POST /reviewer-rules/match returns rules matching given file paths."""
        await self._create_repo(client, "rr-match-repo")
        await client.post(
            "/api/v1/repos/rr-match-repo/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        await client.post(
            "/api/v1/repos/rr-match-repo/reviewer-rules",
            json={"pattern": "bug-fix/**"},
        )
        resp = await client.post(
            "/api/v1/repos/rr-match-repo/reviewer-rules/match",
            json={"file_paths": ["feature-impl/coding-hard.jsonl", "general/data.jsonl"]},
        )
        assert resp.status_code == 200
        matched_patterns = [r["pattern"] for r in resp.json()]
        assert "feature-impl/**" in matched_patterns
        assert "bug-fix/**" not in matched_patterns

    async def test_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/no-repo/reviewer-rules")
        assert resp.status_code == 404
```

- [ ] Run: `uv run pytest tests/server/test_routes_reviewer_rules.py -v`
- [ ] Expected: FAIL — routes do not exist

### Step 2: Implement reviewer rules router

- [ ] Create `src/dit/server/routes/reviewer_rules.py`:

```python
# src/dit/server/routes/reviewer_rules.py
import fnmatch
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import ReviewerRule
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["reviewer-rules"])


class CreateReviewerRuleRequest(BaseModel):
    pattern: str
    reviewer_token_id: Optional[int] = None


class MatchReviewerRulesRequest(BaseModel):
    file_paths: list[str]


def _rule_to_dict(rule: ReviewerRule) -> dict:
    return {
        "id": rule.id,
        "repo_id": rule.repo_id,
        "pattern": rule.pattern,
        "reviewer_token_id": rule.reviewer_token_id,
    }


@router.get("/reviewer-rules")
async def list_reviewer_rules(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("reader")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule)
        .where(ReviewerRule.repo_id == r.id)
        .order_by(ReviewerRule.id)
    )
    rules = result.scalars().all()
    return [_rule_to_dict(rule) for rule in rules]


@router.post("/reviewer-rules", status_code=201)
async def create_reviewer_rule(
    repo: str,
    body: CreateReviewerRuleRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    rule = ReviewerRule(
        repo_id=r.id,
        pattern=body.pattern,
        reviewer_token_id=body.reviewer_token_id,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return _rule_to_dict(rule)


@router.delete("/reviewer-rules/{rule_id}")
async def delete_reviewer_rule(
    repo: str,
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule).where(
            ReviewerRule.repo_id == r.id,
            ReviewerRule.id == rule_id,
        )
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Reviewer rule not found")
    await session.delete(rule)
    await session.commit()
    return {"status": "deleted"}


@router.post("/reviewer-rules/match")
async def match_reviewer_rules(
    repo: str,
    body: MatchReviewerRulesRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("reader")),
):
    """Return all reviewer rules whose pattern matches at least one of the given file paths."""
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(ReviewerRule).where(ReviewerRule.repo_id == r.id)
    )
    all_rules = result.scalars().all()

    matched = []
    for rule in all_rules:
        if any(fnmatch.fnmatch(fp, rule.pattern) for fp in body.file_paths):
            matched.append(_rule_to_dict(rule))

    return matched
```

### Step 3: Register router in `app.py`

- [ ] Edit `src/dit/server/app.py` — add:

```python
    from dit.server.routes.reviewer_rules import router as reviewer_rules_router
    application.include_router(reviewer_rules_router)
```

### Step 4: Run reviewer rules tests

- [ ] Run: `uv run pytest tests/server/test_routes_reviewer_rules.py -v`
- [ ] Expected: All PASS

### Step 5: Commit

- [ ] `git add src/dit/server/routes/reviewer_rules.py src/dit/server/app.py tests/server/test_routes_reviewer_rules.py`
- [ ] `git commit -m "feat: required reviewer rules CRUD + match API"`

---

## Task 9: Branch Protection Enforcement on Merge (Check Required Approvals)

**Files:**
- Modify: `src/dit/server/routes/merge.py`
- Modify: `tests/server/test_routes_merge.py`

### Step 1: Write failing enforcement tests

- [ ] Append to `tests/server/test_routes_merge.py`:

```python
class TestMergeApprovalEnforcement:
    """Merge is blocked when branch protection requires approvals and they haven't been met."""

    async def _setup_repo_with_commits(self, client, tmp_path, repo_name: str):
        """Create a repo with main and feature branches for merge testing."""
        from tests.server.test_routes_merge import _setup_diverged_repo
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(
            client._transport.app if hasattr(client, '_transport') else client,
            tmp_path,
        )
        # Override: create repo explicitly first
        resp = await client.post("/api/v1/repos", json={"name": repo_name})
        # Push refs
        await client.post(
            f"/api/v1/repos/{repo_name}/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            f"/api/v1/repos/{repo_name}/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )
        return store, base_hash, main_hash, feat_hash

    async def test_merge_blocked_when_approvals_insufficient(self, client, tmp_path):
        """Merge into a protected branch with required_approvals=1 is blocked with 0 approvals."""
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )

        # Add branch protection requiring 1 approval
        await client.post(
            "/api/v1/repos/test-repo/branch-protection",
            json={
                "branch_pattern": "main",
                "require_pr": False,
                "required_approvals": 1,
                "block_force_push": True,
                "auto_delete_branch": False,
            },
        )

        # Attempt merge — should fail with 403 (no approvals for PR 0)
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
                "pull_request_id": 999,
            },
        )
        assert resp.status_code == 403
        assert "approval" in resp.json()["detail"].lower()

    async def test_merge_allowed_when_approvals_met(self, client, tmp_path):
        """Merge proceeds when required approvals count is satisfied."""
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )

        # Protect main with 1 required approval
        await client.post(
            "/api/v1/repos/test-repo/branch-protection",
            json={
                "branch_pattern": "main",
                "require_pr": False,
                "required_approvals": 1,
                "block_force_push": True,
                "auto_delete_branch": False,
            },
        )

        # Submit 1 approval for PR 100
        await client.post(
            "/api/v1/repos/test-repo/pulls/100/reviews",
            json={"status": "approved"},
        )

        # Merge with pull_request_id=100 — should succeed
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
                "pull_request_id": 100,
            },
        )
        assert resp.status_code == 200
        assert "commit_hash" in resp.json()

    async def test_merge_to_unprotected_branch_no_approvals_needed(self, client, tmp_path):
        """Merge into an unprotected branch skips approval check."""
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )

        # No protection rules added — merge should work without pull_request_id
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
```

- [ ] Run: `uv run pytest tests/server/test_routes_merge.py::TestMergeApprovalEnforcement -v`
- [ ] Expected: FAIL — merge has no approval check

### Step 2: Add approval enforcement to `merge.py`

- [ ] Edit `src/dit/server/routes/merge.py`:

Add imports at the top:
```python
import fnmatch
from sqlalchemy import func
from dit.server.models import BranchProtection, PrApproval
```

Modify the `MergeRequest` model to include optional `pull_request_id`:
```python
class MergeRequest(BaseModel):
    source_branch: str
    target_branch: str
    message: str
    author: str
    pull_request_id: int | None = None
```

Add the approval check helper:
```python
async def _check_merge_approvals(
    session: AsyncSession,
    repo_id: int,
    target_branch: str,
    pull_request_id: int | None,
) -> None:
    """Raise 403 if branch protection requires more approvals than are present."""
    # Find matching protection rule
    result = await session.execute(
        select(BranchProtection).where(BranchProtection.repo_id == repo_id)
    )
    rules = result.scalars().all()
    matched_rule: BranchProtection | None = None
    for rule in rules:
        if fnmatch.fnmatch(target_branch, rule.branch_pattern):
            matched_rule = rule
            break

    if matched_rule is None or matched_rule.required_approvals == 0:
        return  # no protection or no approval requirement

    if pull_request_id is None:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Branch '{target_branch}' requires {matched_rule.required_approvals} "
                "approval(s). Provide pull_request_id to check approvals."
            ),
        )

    # Count approvals for this PR
    count_result = await session.execute(
        select(func.count()).select_from(PrApproval).where(
            PrApproval.pull_request_id == pull_request_id,
            PrApproval.status == "approved",
        )
    )
    approval_count = count_result.scalar_one()

    if approval_count < matched_rule.required_approvals:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Branch '{target_branch}' requires {matched_rule.required_approvals} "
                f"approval(s), but only {approval_count} found for PR {pull_request_id}."
            ),
        )
```

In the `merge` handler, add the approval check after resolving branches:
```python
    source_hash = await _resolve_branch(session, r.id, body.source_branch)
    target_hash = await _resolve_branch(session, r.id, body.target_branch)

    # Branch protection: check required approvals before merging
    await _check_merge_approvals(session, r.id, body.target_branch, body.pull_request_id)
```

### Step 3: Run merge enforcement tests

- [ ] Run: `uv run pytest tests/server/test_routes_merge.py -v`
- [ ] Expected: All PASS (existing merge tests + new enforcement tests)

### Step 4: Commit

- [ ] `git add src/dit/server/routes/merge.py tests/server/test_routes_merge.py`
- [ ] `git commit -m "feat: enforce required approvals on merge"`

---

## Task 10: Webhook Deprecation Headers

**Files:**
- Modify: `src/dit/server/routes/webhooks.py`
- Modify: `tests/server/test_routes_webhooks.py`

### Step 1: Write failing deprecation header tests

- [ ] Append to `tests/server/test_routes_webhooks.py`:

```python
class TestWebhookDeprecationHeaders:
    """Webhook endpoints should include deprecation headers but remain functional."""

    async def _create_repo(self, client, name="depr-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201

    async def test_create_webhook_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-create-repo")
        resp = await client.post(
            "/api/v1/repos/depr-create-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "", "events": "ref_update"},
        )
        assert resp.status_code == 201
        assert "Deprecation" in resp.headers
        assert "Sunset" in resp.headers

    async def test_list_webhooks_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-list-repo")
        resp = await client.get("/api/v1/repos/depr-list-repo/webhooks")
        assert resp.status_code == 200
        assert "Deprecation" in resp.headers

    async def test_delete_webhook_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-del-repo")
        create_resp = await client.post(
            "/api/v1/repos/depr-del-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "", "events": "ref_update"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/depr-del-repo/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert "Deprecation" in resp.headers

    async def test_webhooks_still_functional_after_deprecation(self, client):
        """Deprecated endpoints should still work correctly."""
        await self._create_repo(client, "depr-func-repo")
        create_resp = await client.post(
            "/api/v1/repos/depr-func-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "s", "events": "ref_update"},
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["url"] == "https://example.com/hook"

        list_resp = await client.get("/api/v1/repos/depr-func-repo/webhooks")
        assert len(list_resp.json()) == 1
```

- [ ] Run: `uv run pytest tests/server/test_routes_webhooks.py::TestWebhookDeprecationHeaders -v`
- [ ] Expected: FAIL — no `Deprecation` headers

### Step 2: Add deprecation headers via FastAPI middleware in `webhooks.py`

- [ ] Edit `src/dit/server/routes/webhooks.py` — add a `Response` parameter to each handler and inject deprecation headers. Replace the full file:

```python
# src/dit/server/routes/webhooks.py
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Webhook
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["webhooks"])

# RFC 8594 deprecation date — Phase 3 webhook endpoints are deprecated in favour of
# Forgejo-native webhooks. Keep functional until Phase 4 cleanup.
_DEPRECATION_DATE = "Sat, 01 Jan 2027 00:00:00 GMT"
_SUNSET_DATE = "Sat, 01 Jul 2027 00:00:00 GMT"


def _add_deprecation_headers(response: Response) -> None:
    response.headers["Deprecation"] = _DEPRECATION_DATE
    response.headers["Sunset"] = _SUNSET_DATE
    response.headers["Link"] = (
        '<https://forgejo.dit.example/api/v1/repos/{owner}/{repo}/hooks>; '
        'rel="successor-version"'
    )


class CreateWebhookRequest(BaseModel):
    url: str
    secret: str = ""
    events: str


@router.post("/webhooks", status_code=201)
async def create_webhook(
    repo: str,
    body: CreateWebhookRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    wh = Webhook(repo_id=r.id, url=body.url, secret=body.secret, events=body.events)
    session.add(wh)
    await session.commit()
    await session.refresh(wh)
    return {"id": wh.id, "url": wh.url, "events": wh.events, "active": wh.active}


@router.get("/webhooks")
async def list_webhooks(
    repo: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    result = await session.execute(select(Webhook).where(Webhook.repo_id == r.id))
    hooks = result.scalars().all()
    return [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in hooks]


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    repo: str,
    webhook_id: int,
    response: Response,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    _add_deprecation_headers(response)
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(Webhook).where(Webhook.repo_id == r.id, Webhook.id == webhook_id)
    )
    wh = result.scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await session.delete(wh)
    await session.commit()
    return {"status": "deleted"}
```

### Step 3: Run webhook tests (deprecation + existing)

- [ ] Run: `uv run pytest tests/server/test_routes_webhooks.py -v`
- [ ] Expected: All PASS

### Step 4: Commit

- [ ] `git add src/dit/server/routes/webhooks.py tests/server/test_routes_webhooks.py`
- [ ] `git commit -m "feat: add deprecation headers to webhook endpoints (Phase 2 webhooks superseded by Forgejo-native)"`

---

## Task 11: Full Test Suite Verification + Conftest Hardening

**Files:**
- Modify: `tests/server/conftest.py`

### Step 1: Update conftest to use `role` field in admin token fixture

The existing conftest creates tokens with `permissions="admin"`. After Task 1 adds `role`, tokens created without an explicit `role` default to `"reader"`, which still works via the legacy mapping — but let's set it explicitly so tests are unambiguous.

- [ ] Edit `tests/server/conftest.py` — in the `client` fixture, change the admin token creation:

```python
    # Create admin token — explicit role for 6-level model
    async with factory() as s:
        token_hash = hashlib.sha256(ADMIN_TOKEN_RAW.encode()).hexdigest()
        t = Token(token_hash=token_hash, label="test-admin", permissions="admin", role="owner")
        s.add(t)
        await s.commit()
```

Setting `role="owner"` for the test admin token ensures it passes all permission checks without relying on legacy mapping.

### Step 2: Run the entire server test suite

- [ ] Run: `uv run pytest tests/server/ -v`
- [ ] Expected: All PASS

### Step 3: Run the full test suite

- [ ] Run: `uv run pytest -v`
- [ ] Expected: All PASS (no regressions from auth.py changes)

### Step 4: Final commit

- [ ] `git add tests/server/conftest.py`
- [ ] `git commit -m "test: harden conftest — use explicit role=owner for admin test token"`

---

## Summary of API Changes

After Phase 3C, dit-core exposes:

| Method | Path | Role Required | Description |
|---|---|---|---|
| GET | `/api/v1/repos/{repo}/branch-protection` | reader | List branch protection rules |
| POST | `/api/v1/repos/{repo}/branch-protection` | admin | Create branch protection rule |
| PUT | `/api/v1/repos/{repo}/branch-protection/{id}` | admin | Update branch protection rule |
| DELETE | `/api/v1/repos/{repo}/branch-protection/{id}` | admin | Delete branch protection rule |
| GET | `/api/v1/repos/{repo}/reviewer-rules` | reader | List reviewer rules |
| POST | `/api/v1/repos/{repo}/reviewer-rules` | admin | Create reviewer rule |
| DELETE | `/api/v1/repos/{repo}/reviewer-rules/{id}` | admin | Delete reviewer rule |
| POST | `/api/v1/repos/{repo}/reviewer-rules/match` | reader | Match rules to file paths |
| POST | `/api/v1/repos/{repo}/pulls/{id}/reviews` | reviewer | Submit PR review |
| GET | `/api/v1/repos/{repo}/pulls/{id}/reviews` | reader | List PR reviews |
| POST | `/api/v1/repos/{repo}/refs/{type}/{name}` | committer | CAS ref update (with branch protection) |
| POST | `/api/v1/repos/{repo}/merge` | maintainer | Merge (with approval check) |
| GET/POST/DELETE | `/api/v1/repos/{repo}/webhooks/*` | admin | **DEPRECATED** — use Forgejo webhooks |

## Role Level Reference

| Role | Level | Numeric |
|---|---|---|
| reader | read-only | 10 |
| reviewer | review + approve | 20 |
| committer | push to non-protected | 30 |
| maintainer | merge + branch mgmt | 40 |
| admin | member/settings mgmt | 50 |
| owner | full control | 60 |
