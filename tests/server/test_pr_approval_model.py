import pytest
from dit.server.models import PrApproval, Repo, Token


class TestPrApprovalModel:
    async def test_create_approval(self, session):
        token = Token(token_hash="a" * 64, label="reviewer-token", permissions="push")
        repo = Repo(name="approval-model-repo")
        session.add_all([repo, token])
        await session.commit()
        await session.refresh(repo)
        await session.refresh(token)

        approval = PrApproval(
            repo_id=repo.id,
            pull_request_id=42,
            token_id=token.id,
            status="approved",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)

        assert approval.id is not None
        assert approval.repo_id == repo.id
        assert approval.pull_request_id == 42
        assert approval.token_id == token.id
        assert approval.status == "approved"
        assert approval.created_at is not None
        assert "PrApproval" in repr(approval)

    async def test_create_changes_requested(self, session):
        token = Token(token_hash="b" * 64, label="reviewer-token-2", permissions="push")
        repo = Repo(name="approval-model-repo-2")
        session.add_all([repo, token])
        await session.commit()
        await session.refresh(repo)
        await session.refresh(token)

        approval = PrApproval(
            repo_id=repo.id,
            pull_request_id=99,
            token_id=token.id,
            status="changes_requested",
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)

        assert approval.status == "changes_requested"
        assert approval.pull_request_id == 99
