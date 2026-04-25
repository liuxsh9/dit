"""Create data_reviewer_rule table
Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "data_reviewer_rule",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("repo_id", sa.BigInteger(), nullable=False),
        sa.Column("pattern", sa.String(256), nullable=False),
        sa.Column("reviewer_token_id", sa.BigInteger(), sa.ForeignKey("dit.tokens.id"), nullable=True),
        schema="dit",
    )

def downgrade() -> None:
    op.drop_table("data_reviewer_rule", schema="dit")
