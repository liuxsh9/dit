import hashlib
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
service_token_header = APIKeyHeader(name="X-Service-Token", auto_error=False)

ROLE_LEVELS: dict[str, int] = {
    "reader": 10,
    "reviewer": 20,
    "committer": 30,
    "maintainer": 40,
    "admin": 50,
    "owner": 60,
}

_LEGACY_PERM_MAP: dict[str, str] = {
    "read": "reader",
    "push": "committer",
    "admin": "admin",
}


def _token_level(token: "Token") -> int:
    if hasattr(token, 'role') and token.role and token.role != "reader":
        return ROLE_LEVELS.get(token.role, 10)
    if token.permissions in _LEGACY_PERM_MAP:
        mapped_role = _LEGACY_PERM_MAP[token.permissions]
        return ROLE_LEVELS.get(mapped_role, 10)
    return 10


async def get_session() -> AsyncSession:
    raise NotImplementedError("Must be overridden via dependency_overrides")


def _synthetic_admin_token() -> Token:
    return Token(
        token_hash="",
        label="service-token",
        repo_scope=None,
        permissions="admin",
        role="owner",
        created_at=datetime.now(timezone.utc),
        expires_at=None,
    )


async def verify_token(
    request: Request,
    authorization: str | None = Depends(api_key_header),
    x_service_token: str | None = Depends(service_token_header),
    session: AsyncSession = Depends(get_session),
) -> Token:
    # Check service token first (short-circuit — no DB access needed)
    configured_service_token: str = getattr(
        getattr(request.app.state, "settings", None), "service_token", ""
    )
    if x_service_token and configured_service_token:
        if secrets.compare_digest(x_service_token, configured_service_token):
            return _synthetic_admin_token()
        raise HTTPException(status_code=401, detail="Invalid service token")

    # Fall through to Bearer token auth
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    raw_token = authorization.removeprefix("Bearer ").removeprefix("token ").strip()
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
    required_level = ROLE_LEVELS.get(required)
    if required_level is None:
        # Legacy compatibility: map old permission names to new roles
        legacy_role = _LEGACY_PERM_MAP.get(required)
        if legacy_role:
            required_level = ROLE_LEVELS[legacy_role]
        else:
            raise ValueError(f"Unknown role: {required!r}")

    async def check(token: Token = Depends(verify_token)) -> Token:
        if _token_level(token) < required_level:
            raise HTTPException(status_code=403, detail=f"Requires {required} permission")
        return token

    return check
