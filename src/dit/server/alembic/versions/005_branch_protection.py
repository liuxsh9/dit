"""Branch protection table + role column on tokens

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("role", sa.String(32), nullable=False, server_default="reader"), schema="datahub")
    op.create_table(
        "branch_protection",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), sa.ForeignKey("datahub.repos.id"), nullable=False),
        sa.Column("branch_pattern", sa.String(256), nullable=False),
        sa.Column("require_pr", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("required_approvals", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("block_force_push", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("auto_delete_branch", sa.Boolean(), nullable=False, server_default="false"),
        sa.UniqueConstraint("repo_id", "branch_pattern", name="uq_branch_protection_repo_pattern"),
        schema="datahub",
    )


def downgrade() -> None:
    op.drop_table("branch_protection", schema="datahub")
    op.drop_column("tokens", "role", schema="datahub")
