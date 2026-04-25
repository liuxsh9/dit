import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from dit.server.webhooks import load_webhooks, fire_webhook_payloads, WebhookEvent


class TestFireWebhooks:
    async def test_fire_sends_post(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="s3cret", events="ref_update")
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 1

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()

            await fire_webhook_payloads(
                hooks=hooks,
                event=WebhookEvent.REF_UPDATE,
                payload={"ref": "heads/main", "old_hash": "a" * 64, "new_hash": "b" * 64},
            )

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://example.com/hook"
            body = call_args[1]["content"]
            headers = call_args[1]["headers"]
            assert "X-Dit-Signature" in headers

    async def test_fire_skips_inactive(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="", events="ref_update", active=False)
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 0

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await fire_webhook_payloads(hooks=hooks, event=WebhookEvent.REF_UPDATE, payload={})
            mock_client.post.assert_not_called()

    async def test_fire_skips_non_matching_event(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="", events="branch_create")
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 0

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await fire_webhook_payloads(hooks=hooks, event=WebhookEvent.REF_UPDATE, payload={})
            mock_client.post.assert_not_called()
