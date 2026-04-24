import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


def _mock_request(service_token: str = ""):
    """Build a minimal mock Request with app.state.settings.service_token."""
    settings = MagicMock()
    settings.service_token = service_token
    state = MagicMock()
    state.settings = settings
    app = MagicMock()
    app.state = state
    request = MagicMock()
    request.app = app
    return request


class TestAuth:
    async def test_valid_token(self, session: AsyncSession):
        raw = "test-token-123"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="test", permissions="push")
        session.add(token)
        await session.commit()

        from dit.server.auth import verify_token

        result = await verify_token(
            request=_mock_request(),
            authorization=f"Bearer {raw}",
            x_service_token=None,
            session=session,
        )
        assert result.label == "test"
        assert result.permissions == "push"

    async def test_missing_auth(self, session: AsyncSession):
        from fastapi import HTTPException
        from dit.server.auth import verify_token

        with pytest.raises(HTTPException) as exc_info:
            await verify_token(
                request=_mock_request(),
                authorization=None,
                x_service_token=None,
                session=session,
            )
        assert exc_info.value.status_code == 401

    async def test_invalid_token(self, session: AsyncSession):
        from fastapi import HTTPException
        from dit.server.auth import verify_token

        with pytest.raises(HTTPException) as exc_info:
            await verify_token(
                request=_mock_request(),
                authorization="Bearer bad-token",
                x_service_token=None,
                session=session,
            )
        assert exc_info.value.status_code == 401

    async def test_expired_token(self, session: AsyncSession):
        raw = "expired-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(
            token_hash=token_hash,
            label="expired",
            permissions="push",
            expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(token)
        await session.commit()

        from fastapi import HTTPException
        from dit.server.auth import verify_token

        with pytest.raises(HTTPException) as exc_info:
            await verify_token(
                request=_mock_request(),
                authorization=f"Bearer {raw}",
                x_service_token=None,
                session=session,
            )
        assert exc_info.value.status_code == 403

    async def test_require_permission_sufficient(self, session: AsyncSession):
        raw = "admin-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="admin", permissions="admin")
        session.add(token)
        await session.commit()

        from dit.server.auth import verify_token, require_permission

        tok = await verify_token(
            request=_mock_request(),
            authorization=f"Bearer {raw}",
            x_service_token=None,
            session=session,
        )
        # admin >= push, should not raise
        checker = require_permission("push")
        result = await checker(token=tok)
        assert result.permissions == "admin"

    async def test_require_permission_insufficient(self, session: AsyncSession):
        raw = "read-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="reader", permissions="read")
        session.add(token)
        await session.commit()

        from fastapi import HTTPException
        from dit.server.auth import verify_token, require_permission

        tok = await verify_token(
            request=_mock_request(),
            authorization=f"Bearer {raw}",
            x_service_token=None,
            session=session,
        )
        checker = require_permission("admin")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403
