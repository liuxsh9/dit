"""Create ci_checks table

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ci_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_id", sa.Integer(), sa.ForeignKey("dit.repos.id"), nullable=False),
        sa.Column("commit_hash", sa.String(64), nullable=False),
        sa.Column("check_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repo_id", "commit_hash", "check_name", name="uq_ci_check"),
        schema="dit",
    )
    op.create_index("ix_ci_checks_repo_id", "ci_checks", ["repo_id"], schema="dit")


def downgrade() -> None:
    op.drop_index("ix_ci_checks_repo_id", table_name="ci_checks", schema="dit")
    op.drop_table("ci_checks", schema="dit")
