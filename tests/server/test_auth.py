import hashlib
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


class TestAuth:
    async def test_valid_token(self, session: AsyncSession):
        raw = "test-token-123"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="test", permissions="push")
        session.add(token)
        await session.commit()

        from dit.server.auth import verify_token

        # Manually call verify_token with the right args
        result = await verify_token(
            authorization=f"Bearer {raw}",
            session=session,
        )
        assert result.label == "test"
        assert result.permissions == "push"

    async def test_missing_auth(self, session: AsyncSession):
        from fastapi import HTTPException
        from dit.server.auth import verify_token

        with pytest.raises(HTTPException) as exc_info:
            await verify_token(authorization=None, session=session)
        assert exc_info.value.status_code == 401

    async def test_invalid_token(self, session: AsyncSession):
        from fastapi import HTTPException
        from dit.server.auth import verify_token

        with pytest.raises(HTTPException) as exc_info:
            await verify_token(authorization="Bearer bad-token", session=session)
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
            await verify_token(authorization=f"Bearer {raw}", session=session)
        assert exc_info.value.status_code == 403

    async def test_require_permission_sufficient(self, session: AsyncSession):
        raw = "admin-token"
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        token = Token(token_hash=token_hash, label="admin", permissions="admin")
        session.add(token)
        await session.commit()

        from dit.server.auth import verify_token, require_permission

        tok = await verify_token(authorization=f"Bearer {raw}", session=session)
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

        tok = await verify_token(authorization=f"Bearer {raw}", session=session)
        checker = require_permission("admin")
        with pytest.raises(HTTPException) as exc_info:
            await checker(token=tok)
        assert exc_info.value.status_code == 403
