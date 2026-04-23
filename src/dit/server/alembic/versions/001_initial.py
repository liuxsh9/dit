"""Initial schema: repos, refs, tokens

Revision ID: 001
Revises: None
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS datahub")

    op.create_table(
        "repos",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(128), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="datahub",
    )

    op.create_table(
        "refs",
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("datahub.repos.id"), primary_key=True),
        sa.Column("name", sa.String(256), primary_key=True),
        sa.Column("target_hash", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="datahub",
    )

    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("label", sa.String(128), nullable=False),
        sa.Column("repo_scope", sa.Integer, sa.ForeignKey("datahub.repos.id"), nullable=True),
        sa.Column("permissions", sa.String(32), nullable=False, server_default="push"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="datahub",
    )


def downgrade() -> None:
    op.drop_table("tokens", schema="datahub")
    op.drop_table("refs", schema="datahub")
    op.drop_table("repos", schema="datahub")
    op.execute("DROP SCHEMA IF EXISTS datahub")
