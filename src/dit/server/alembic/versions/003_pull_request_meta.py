"""Add data_pull_request_meta table

Revision ID: 003
Revises: 002
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "data_pull_request_meta",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("dit.repos.id"), nullable=False),
        sa.Column("pull_request_id", sa.BigInteger, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("author", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("source_ref", sa.String(256), nullable=False),
        sa.Column("target_ref", sa.String(256), nullable=False),
        sa.Column("base_commit", sa.String(64), nullable=False),
        sa.Column("source_commit", sa.String(64), nullable=False),
        sa.Column("target_commit", sa.String(64), nullable=False),
        sa.Column("merge_commit", sa.String(64), nullable=True),
        sa.Column("is_mergeable", sa.Boolean, nullable=True),
        sa.Column("conflict_files", sa.Text, nullable=True),
        sa.Column("stats_added", sa.Integer, server_default=sa.text("0")),
        sa.Column("stats_removed", sa.Integer, server_default=sa.text("0")),
        sa.Column("stats_refreshed", sa.Integer, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repo_id", "pull_request_id", name="uq_pr_repo_prid"),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("data_pull_request_meta", schema="dit")
