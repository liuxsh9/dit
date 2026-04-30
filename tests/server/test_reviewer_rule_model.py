from dit.server.models import ReviewerRule, Repo, Token


class TestReviewerRuleModel:
    async def test_create_reviewer_rule(self, session):
        repo = Repo(name="rr-model-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        token = Token(
            token_hash="a" * 64,
            label="rr-token",
            permissions="read",
            role="reader",
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)

        rule = ReviewerRule(
            repo_id=repo.id,
            pattern="feature-impl/**",
            reviewer_token_id=token.id,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        assert rule.id is not None
        assert rule.repo_id == repo.id
        assert rule.pattern == "feature-impl/**"
        assert rule.reviewer_token_id == token.id
        assert "ReviewerRule" in repr(rule)

    async def test_reviewer_rule_pattern_only(self, session):
        repo = Repo(name="rr-no-token-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        rule = ReviewerRule(
            repo_id=repo.id,
            pattern="docs/**",
            reviewer_token_id=None,
        )
        session.add(rule)
        await session.commit()
        await session.refresh(rule)

        assert rule.id is not None
        assert rule.reviewer_token_id is None
        assert rule.pattern == "docs/**"
