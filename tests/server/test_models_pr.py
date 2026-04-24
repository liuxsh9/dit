import pytest
from sqlalchemy import select

from dit.server.models import PullRequestMeta, Repo, PrComment


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


class TestPrCommentModel:
    async def test_create_general_comment(self, session):
        repo = Repo(name="comment-repo")
        session.add(repo)
        await session.flush()
        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()
        comment = PrComment(
            pull_request_meta_id=pr.id, author="reviewer1", body="Looks good overall!",
        )
        session.add(comment)
        await session.commit()
        await session.refresh(comment)
        assert comment.id is not None
        assert comment.body == "Looks good overall!"
        assert comment.file_path is None
        assert comment.row_hash is None
        assert comment.field_path is None
        assert comment.change_type is None
        assert comment.created_at is not None

    async def test_create_row_level_comment(self, session):
        repo = Repo(name="row-comment-repo")
        session.add(repo)
        await session.flush()
        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()
        comment = PrComment(
            pull_request_meta_id=pr.id, author="reviewer2",
            body="This response has wrong formatting",
            file_path="train/data.jsonl", row_hash="d" * 64,
            field_path="messages[1].content", change_type="added",
        )
        session.add(comment)
        await session.commit()
        await session.refresh(comment)
        assert comment.file_path == "train/data.jsonl"
        assert comment.row_hash == "d" * 64
        assert comment.field_path == "messages[1].content"
        assert comment.change_type == "added"

    async def test_multiple_comments_on_same_row(self, session):
        repo = Repo(name="multi-comment-repo")
        session.add(repo)
        await session.flush()
        pr = PullRequestMeta(
            repo_id=repo.id, pull_request_id=1, title="T", author="a",
            status="open", source_ref="heads/f", target_ref="heads/m",
            base_commit="a" * 64, source_commit="b" * 64, target_commit="c" * 64,
        )
        session.add(pr)
        await session.flush()
        c1 = PrComment(
            pull_request_meta_id=pr.id, author="r1", body="Comment 1",
            file_path="data.jsonl", row_hash="e" * 64, change_type="added",
        )
        c2 = PrComment(
            pull_request_meta_id=pr.id, author="r2", body="Comment 2",
            file_path="data.jsonl", row_hash="e" * 64, change_type="added",
        )
        session.add_all([c1, c2])
        await session.commit()
        from sqlalchemy import select
        result = await session.execute(
            select(PrComment).where(
                PrComment.pull_request_meta_id == pr.id,
                PrComment.row_hash == "e" * 64,
            )
        )
        comments = result.scalars().all()
        assert len(comments) == 2
