"""Scope PR approvals by repository
Revision ID: 008
Revises: 007
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pr_approval",
        sa.Column("repo_id", sa.BigInteger(), nullable=True),
        schema="dit",
    )
    op.execute(
        """
        DELETE FROM dit.pr_approval AS approval
        WHERE NOT EXISTS (
            SELECT 1
            FROM dit.data_pull_request_meta AS pr
            WHERE pr.pull_request_id = approval.pull_request_id
        )
        """
    )
    op.execute(
        """
        UPDATE dit.pr_approval AS approval
        SET repo_id = (
            SELECT MIN(pr.repo_id)
            FROM dit.data_pull_request_meta AS pr
            WHERE pr.pull_request_id = approval.pull_request_id
        )
        WHERE approval.repo_id IS NULL
        """
    )
    op.execute(
        """
        DELETE FROM dit.pr_approval AS approval
        WHERE (
            SELECT COUNT(*)
            FROM dit.data_pull_request_meta AS pr
            WHERE pr.pull_request_id = approval.pull_request_id
        ) > 1
        """
    )
    op.alter_column("pr_approval", "repo_id", nullable=False, schema="dit")
    op.create_foreign_key(
        "fk_pr_approval_repo_id",
        "pr_approval",
        "repos",
        ["repo_id"],
        ["id"],
        source_schema="dit",
        referent_schema="dit",
    )
    op.drop_constraint("uq_pr_approval_pr_token", "pr_approval", schema="dit", type_="unique")
    op.create_unique_constraint(
        "uq_pr_approval_repo_pr_token",
        "pr_approval",
        ["repo_id", "pull_request_id", "token_id"],
        schema="dit",
    )


def downgrade() -> None:
    op.drop_constraint("uq_pr_approval_repo_pr_token", "pr_approval", schema="dit", type_="unique")
    op.create_unique_constraint(
        "uq_pr_approval_pr_token",
        "pr_approval",
        ["pull_request_id", "token_id"],
        schema="dit",
    )
    op.drop_constraint("fk_pr_approval_repo_id", "pr_approval", schema="dit", type_="foreignkey")
    op.drop_column("pr_approval", "repo_id", schema="dit")
