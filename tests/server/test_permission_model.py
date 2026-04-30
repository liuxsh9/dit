"""Tests for the 6-level permission model."""
import hashlib
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token
from dit.server.auth import verify_token, require_permission, ROLE_LEVELS


def _mock_request(service_token: str = ""):
    settings = MagicMock()
    settings.service_token = service_token
    state = MagicMock()
    state.settings = settings
    app = MagicMock()
    app.state = state
    request = MagicMock()
    request.app = app
    return request


async def _make_token(session: AsyncSession, raw: str, permissions: str = "push", role: str = "reader") -> Token:
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = Token(token_hash=token_hash, label=f"token-{raw[:8]}", permissions=permissions, role=role)
    session.add(token)
    await session.commit()
    return token


async def _verify(session: AsyncSession, raw: str) -> Token:
    return await verify_token(
        request=_mock_request(),
        authorization=f"Bearer {raw}",
        x_service_token=None,
        session=session,
    )


class TestTokenRoleField:
    async def test_token_has_role_field(self, session: AsyncSession):
        """Token model has a role field."""
        raw = "role-field-test"
        await _make_token(session, raw, permissions="push", role="committer")
        tok = await _verify(session, raw)
        assert hasattr(tok, "role")
        assert tok.role == "committer"

    async def test_token_role_default_is_reader(self, session: AsyncSession):
        """Default role is 'reader'."""
        token_hash = hashlib.sha256(b"default-role-test").hexdigest()
        token = Token(token_hash=token_hash, label="default-role", permissions="push")
        session.add(token)
        await session.commit()
        tok = await _verify(session, "default-role-test")
        assert tok.role == "reader"


class TestRoleLevels:
    def test_role_levels_ordered(self):
        """ROLE_LEVELS defines 6 levels in correct order."""
        assert ROLE_LEVELS["reader"] < ROLE_LEVELS["reviewer"]
        assert ROLE_LEVELS["reviewer"] < ROLE_LEVELS["committer"]
        assert ROLE_LEVELS["committer"] < ROLE_LEVELS["maintainer"]
        assert ROLE_LEVELS["maintainer"] < ROLE_LEVELS["admin"]
        assert ROLE_LEVELS["admin"] < ROLE_LEVELS["owner"]

    def test_all_six_roles_present(self):
        assert set(ROLE_LEVELS.keys()) >= {"reader", "reviewer", "committer", "maintainer", "admin", "owner"}


class TestOwnerCanDoEverything:
    async def test_owner_passes_all_levels(self, session: AsyncSession):
        """Owner-role token passes every permission check."""
        raw = "owner-token"
        await _make_token(session, raw, permissions="admin", role="owner")
        tok = await _verify(session, raw)

        for role in ("reader", "reviewer", "committer", "maintainer", "admin", "owner"):
            checker = require_permission(role)
            result = await checker(token=tok)
            assert result is tok


class TestReaderCannotCommit:
    async def test_reader_cannot_push(self, session: AsyncSession):
        """Reader cannot do committer (push) operations."""
        raw = "reader-token"
        await _make_token(session, raw, permissions="read", role="reader")
        tok = await _verify(session, raw)

        checker = require_permission("committer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403

    async def test_reader_can_read(self, session: AsyncSession):
        """Reader can do reader operations."""
        raw = "reader-read-token"
        await _make_token(session, raw, permissions="read", role="reader")
        tok = await _verify(session, raw)

        checker = require_permission("reader")
        result = await checker(token=tok)
        assert result is tok


class TestReviewerPermissions:
    async def test_reviewer_can_read(self, session: AsyncSession):
        """Reviewer can read."""
        raw = "reviewer-read-token"
        await _make_token(session, raw, permissions="read", role="reviewer")
        tok = await _verify(session, raw)

        checker = require_permission("reader")
        result = await checker(token=tok)
        assert result is tok

    async def test_reviewer_cannot_push(self, session: AsyncSession):
        """Reviewer cannot push (committer level)."""
        raw = "reviewer-push-token"
        await _make_token(session, raw, permissions="read", role="reviewer")
        tok = await _verify(session, raw)

        checker = require_permission("committer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403


class TestCommitterPermissions:
    async def test_committer_can_push(self, session: AsyncSession):
        """Committer can push."""
        raw = "committer-push-token"
        await _make_token(session, raw, permissions="push", role="committer")
        tok = await _verify(session, raw)

        checker = require_permission("committer")
        result = await checker(token=tok)
        assert result is tok

    async def test_committer_cannot_do_maintainer_ops(self, session: AsyncSession):
        """Committer cannot do maintainer-level ops."""
        raw = "committer-maint-token"
        await _make_token(session, raw, permissions="push", role="committer")
        tok = await _verify(session, raw)

        checker = require_permission("maintainer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403


class TestMaintainerPermissions:
    async def test_maintainer_can_merge(self, session: AsyncSession):
        """Maintainer can do maintainer-level ops (merge)."""
        raw = "maintainer-merge-token"
        await _make_token(session, raw, permissions="push", role="maintainer")
        tok = await _verify(session, raw)

        checker = require_permission("maintainer")
        result = await checker(token=tok)
        assert result is tok

    async def test_maintainer_cannot_do_admin_ops(self, session: AsyncSession):
        """Maintainer cannot do admin-level ops."""
        raw = "maintainer-admin-token"
        await _make_token(session, raw, permissions="push", role="maintainer")
        tok = await _verify(session, raw)

        checker = require_permission("admin")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403


class TestAdminBelowOwner:
    async def test_admin_cannot_do_owner_only_ops(self, session: AsyncSession):
        """Admin cannot do owner-level ops."""
        raw = "admin-owner-token"
        await _make_token(session, raw, permissions="admin", role="admin")
        tok = await _verify(session, raw)

        checker = require_permission("owner")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403


class TestLegacyTokenMapping:
    async def test_legacy_push_maps_to_committer(self, session: AsyncSession):
        """Legacy token with permissions='push' and default role maps to committer level."""
        raw = "legacy-push-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        # No explicit role set — defaults to "reader", but permissions="push" legacy mapping applies
        token = Token(token_hash=token_hash, label="legacy-push", permissions="push")
        session.add(token)
        await session.commit()

        tok = await _verify(session, raw)
        # Should be able to do committer-level ops via legacy mapping
        checker = require_permission("committer")
        result = await checker(token=tok)
        assert result is tok

    async def test_legacy_admin_maps_to_admin(self, session: AsyncSession):
        """Legacy token with permissions='admin' maps to admin level."""
        raw = "legacy-admin-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="legacy-admin", permissions="admin")
        session.add(token)
        await session.commit()

        tok = await _verify(session, raw)
        checker = require_permission("admin")
        result = await checker(token=tok)
        assert result is tok

    async def test_legacy_read_maps_to_reader(self, session: AsyncSession):
        """Legacy token with permissions='read' maps to reader level."""
        raw = "legacy-read-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="legacy-read", permissions="read")
        session.add(token)
        await session.commit()

        tok = await _verify(session, raw)
        checker = require_permission("reader")
        result = await checker(token=tok)
        assert result is tok

    async def test_legacy_read_cannot_push(self, session: AsyncSession):
        """Legacy token with permissions='read' cannot do committer ops."""
        raw = "legacy-read-nopush-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="legacy-read-nopush", permissions="read")
        session.add(token)
        await session.commit()

        tok = await _verify(session, raw)
        checker = require_permission("committer")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403

    async def test_legacy_route_require_read(self, session: AsyncSession):
        """Existing routes that call require_permission('read') still work."""
        raw = "legacy-route-read-token"
        await _make_token(session, raw, permissions="read", role="reader")
        tok = await _verify(session, raw)

        checker = require_permission("read")  # legacy name
        result = await checker(token=tok)
        assert result is tok

    async def test_legacy_route_require_push(self, session: AsyncSession):
        """Existing routes that call require_permission('push') still work."""
        raw = "legacy-route-push-token"
        await _make_token(session, raw, permissions="push", role="committer")
        tok = await _verify(session, raw)

        checker = require_permission("push")  # legacy name
        result = await checker(token=tok)
        assert result is tok

    async def test_legacy_route_require_admin(self, session: AsyncSession):
        """Existing routes that call require_permission('admin') still work."""
        raw = "legacy-route-admin-token"
        await _make_token(session, raw, permissions="admin", role="admin")
        tok = await _verify(session, raw)

        checker = require_permission("admin")  # works as both legacy and new name
        result = await checker(token=tok)
        assert result is tok
