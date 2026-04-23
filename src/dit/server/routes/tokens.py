import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, verify_token
from dit.server.models import Token

router = APIRouter(prefix="/api/v1/admin/tokens", tags=["tokens"])


def _require_admin(token: Token = Depends(verify_token)) -> Token:
    if token.permissions != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return token


class TokenCreate(BaseModel):
    label: str
    permissions: str = "push"
    repo_scope: int | None = None


class TokenCreated(BaseModel):
    id: int
    label: str
    permissions: str
    token: str  # raw token — returned only on creation


class TokenRevoked(BaseModel):
    id: int
    deleted: bool


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TokenCreated)
async def create_token(
    body: TokenCreate,
    session: AsyncSession = Depends(get_session),
    _admin: Token = Depends(_require_admin),
) -> TokenCreated:
    raw = "dit_" + secrets.token_hex(32)
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    token = Token(
        token_hash=token_hash,
        label=body.label,
        permissions=body.permissions,
        repo_scope=body.repo_scope,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    return TokenCreated(
        id=token.id,
        label=token.label,
        permissions=token.permissions,
        token=raw,
    )


@router.delete("/{token_id}", response_model=TokenRevoked)
async def revoke_token(
    token_id: int,
    session: AsyncSession = Depends(get_session),
    _admin: Token = Depends(_require_admin),
) -> TokenRevoked:
    result = await session.execute(select(Token).where(Token.id == token_id))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")
    await session.delete(token)
    await session.commit()
    return TokenRevoked(id=token_id, deleted=True)
