import pytest
from dit.server.models import BranchProtection, Repo


class TestBranchProtectionModel:
    async def test_create_branch_protection(self, session):
        repo = Repo(name="bp-model-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        rule = BranchProtection(
            repo_id=repo.id,
            branch_pattern="main",
            require_pr=True,
            required_approvals=2,
            block_force_push=True,
            auto_delete_branch=False,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        assert rule.id is not None
        assert rule.repo_id == repo.id
        assert rule.branch_pattern == "main"
        assert rule.require_pr is True
        assert rule.required_approvals == 2
        assert rule.block_force_push is True
        assert rule.auto_delete_branch is False

    async def test_branch_protection_defaults(self, session):
        repo = Repo(name="bp-defaults-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        rule = BranchProtection(repo_id=repo.id, branch_pattern="release/*")
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        assert rule.require_pr is True
        assert rule.required_approvals == 1
        assert rule.block_force_push is True
        assert rule.auto_delete_branch is False
        assert "BranchProtection" in repr(rule)
