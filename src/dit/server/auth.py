import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


async def get_session() -> AsyncSession:
    raise NotImplementedError("Must be overridden via dependency_overrides")


async def verify_token(
    authorization: str | None = Depends(api_key_header),
    session: AsyncSession = Depends(get_session),
) -> Token:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    raw_token = authorization.removeprefix("Bearer ").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Empty token")

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    result = await session.execute(select(Token).where(Token.token_hash == token_hash))
    token = result.scalar_one_or_none()

    if token is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Token expired")

    return token


def require_permission(required: str):
    """Dependency factory that checks token has required permission level."""
    permission_levels = {"read": 0, "push": 1, "admin": 2}

    async def check(token: Token = Depends(verify_token)) -> Token:
        token_level = permission_levels.get(token.permissions, 0)
        required_level = permission_levels.get(required, 0)
        if token_level < required_level:
            raise HTTPException(status_code=403, detail=f"Requires {required} permission")
        return token

    return check
