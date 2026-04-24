"""Create pr_approval table
Revision ID: 006
Revises: 005
"""
from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "pr_approval",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("pull_request_id", sa.BigInteger(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), sa.ForeignKey("datahub.tokens.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.UniqueConstraint("pull_request_id", "token_id", name="uq_pr_approval_pr_token"),
        schema="datahub",
    )

def downgrade() -> None:
    op.drop_table("pr_approval", schema="datahub")
