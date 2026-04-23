import pytest
from dit.server.models import Repo, Ref, Token


class TestModels:
    async def test_create_repo(self, session):
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)
        assert repo.id is not None
        assert repo.name == "test-repo"
        assert repo.created_at is not None

    async def test_create_ref(self, session):
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        ref = Ref(repo_id=repo.id, name="heads/main", target_hash="a" * 64)
        session.add(ref)
        await session.commit()
        assert ref.target_hash == "a" * 64

    async def test_create_token(self, session):
        token = Token(token_hash="b" * 64, label="test-token", permissions="push")
        session.add(token)
        await session.commit()
        await session.refresh(token)
        assert token.id is not None
        assert token.permissions == "push"

    async def test_repo_repr(self):
        repo = Repo(id=1, name="my-repo")
        assert "my-repo" in repr(repo)

    async def test_token_with_scope(self, session):
        repo = Repo(name="scoped-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        token = Token(token_hash="c" * 64, label="scoped", permissions="read", repo_scope=repo.id)
        session.add(token)
        await session.commit()
        assert token.repo_scope == repo.id
