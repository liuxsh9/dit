"""Add pr_comment table

Revision ID: 004
Revises: 003
Create Date: 2026-04-24
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.create_table(
        "pr_comment",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("pull_request_meta_id", sa.BigInteger,
                  sa.ForeignKey("dit.data_pull_request_meta.id"), nullable=False),
        sa.Column("author", sa.String(256), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("file_path", sa.Text, nullable=True),
        sa.Column("row_hash", sa.String(64), nullable=True),
        sa.Column("field_path", sa.String(256), nullable=True),
        sa.Column("change_type", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="dit",
    )
    op.create_index("ix_pr_comment_pr_meta_id", "pr_comment",
                    ["pull_request_meta_id"], schema="dit")

def downgrade() -> None:
    op.drop_index("ix_pr_comment_pr_meta_id", table_name="pr_comment", schema="dit")
    op.drop_table("pr_comment", schema="dit")
