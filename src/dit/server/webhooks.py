from __future__ import annotations

import enum
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Webhook


class WebhookEvent(str, enum.Enum):
    REF_UPDATE = "ref_update"
    BRANCH_CREATE = "branch_create"
    BRANCH_DELETE = "branch_delete"


async def load_webhooks(
    session: AsyncSession,
    repo_id: int,
    event: WebhookEvent,
) -> list[dict]:
    result = await session.execute(
        select(Webhook).where(Webhook.repo_id == repo_id, Webhook.active == True)
    )
    hooks = result.scalars().all()
    subscribed = [
        {"url": h.url, "secret": h.secret}
        for h in hooks
        if event.value in {e.strip() for e in h.events.split(",")}
    ]
    return subscribed


async def fire_webhook_payloads(
    hooks: list[dict],
    event: WebhookEvent,
    payload: dict,
) -> None:
    if not hooks:
        return

    payload["event"] = event.value
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for hook in hooks:
            signature = hmac.new(
                hook["secret"].encode() if hook["secret"] else b"",
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            try:
                await client.post(
                    hook["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Dit-Signature": signature,
                        "X-Dit-Event": event.value,
                    },
                )
            except Exception:
                pass
