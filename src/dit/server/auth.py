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


async def get_session() -> AsyncSession:
    raise NotImplementedError("Must be overridden via dependency_overrides")


def _synthetic_admin_token() -> Token:
    from sqlalchemy.orm import InstanceState
    t = object.__new__(Token)
    # Bootstrap SQLAlchemy instrumentation without hitting the DB
    state = InstanceState(t, Token.__mapper__)  # type: ignore[arg-type]
    object.__setattr__(t, "_sa_instance_state", state)
    object.__setattr__(t, "id", -1)
    object.__setattr__(t, "token_hash", "")
    object.__setattr__(t, "label", "service-token")
    object.__setattr__(t, "repo_scope", None)
    object.__setattr__(t, "permissions", "admin")
    object.__setattr__(t, "created_at", datetime.now(timezone.utc))
    object.__setattr__(t, "expires_at", None)
    return t


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
    permission_levels = {"read": 0, "push": 1, "admin": 2}

    async def check(token: Token = Depends(verify_token)) -> Token:
        token_level = permission_levels.get(token.permissions, 0)
        required_level = permission_levels.get(required, 0)
        if token_level < required_level:
            raise HTTPException(status_code=403, detail=f"Requires {required} permission")
        return token

    return check
