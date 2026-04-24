import pytest
from sqlalchemy import select

from dit.server.models import PullRequestMeta, Repo


class TestPullRequestMetaModel:
    async def test_create_pr_meta(self, session):
        repo = Repo(name="pr-test-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="Add new training data",
            author="zhangsan",
            status="open",
            source_ref="heads/feature/new-data",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        session.add(pr)
        await session.commit()

        result = await session.execute(
            select(PullRequestMeta).where(PullRequestMeta.id == pr.id)
        )
        loaded = result.scalar_one()
        assert loaded.title == "Add new training data"
        assert loaded.author == "zhangsan"
        assert loaded.status == "open"
        assert loaded.source_ref == "heads/feature/new-data"
        assert loaded.target_ref == "heads/main"
        assert loaded.base_commit == "a" * 64
        assert loaded.source_commit == "b" * 64
        assert loaded.target_commit == "c" * 64
        assert loaded.merge_commit is None
        assert loaded.is_mergeable is None
        assert loaded.conflict_files is None
        assert loaded.stats_added == 0
        assert loaded.stats_removed == 0
        assert loaded.stats_refreshed == 0

    async def test_pr_meta_defaults(self, session):
        repo = Repo(name="pr-defaults-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=2,
            title="Test PR",
            author="tester",
            status="open",
            source_ref="heads/feat",
            target_ref="heads/main",
            base_commit="d" * 64,
            source_commit="e" * 64,
            target_commit="f" * 64,
        )
        session.add(pr)
        await session.commit()
        await session.refresh(pr)

        assert pr.stats_added == 0
        assert pr.stats_removed == 0
        assert pr.stats_refreshed == 0
        assert pr.merge_commit is None
        assert pr.created_at is not None

    async def test_pr_meta_unique_pr_id_per_repo(self, session):
        repo = Repo(name="pr-unique-repo")
        session.add(repo)
        await session.flush()

        pr1 = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="First",
            author="a",
            status="open",
            source_ref="heads/f1",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        pr2 = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=1,
            title="Duplicate",
            author="b",
            status="open",
            source_ref="heads/f2",
            target_ref="heads/main",
            base_commit="d" * 64,
            source_commit="e" * 64,
            target_commit="f" * 64,
        )
        session.add(pr1)
        await session.flush()
        session.add(pr2)
        with pytest.raises(Exception):
            await session.flush()

    async def test_pr_meta_update_stats(self, session):
        repo = Repo(name="pr-update-repo")
        session.add(repo)
        await session.flush()

        pr = PullRequestMeta(
            repo_id=repo.id,
            pull_request_id=3,
            title="Update test",
            author="tester",
            status="open",
            source_ref="heads/feat",
            target_ref="heads/main",
            base_commit="a" * 64,
            source_commit="b" * 64,
            target_commit="c" * 64,
        )
        session.add(pr)
        await session.commit()

        pr.stats_added = 42
        pr.stats_removed = 10
        pr.stats_refreshed = 5
        pr.is_mergeable = True
        pr.merge_commit = "d" * 64
        pr.status = "merged"
        await session.commit()
        await session.refresh(pr)

        assert pr.stats_added == 42
        assert pr.stats_removed == 10
        assert pr.stats_refreshed == 5
        assert pr.is_mergeable is True
        assert pr.merge_commit == "d" * 64
        assert pr.status == "merged"
