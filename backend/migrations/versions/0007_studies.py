"""Studies: a question, its ordered scenarios, and the one the analyst backs.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "study",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question", sa.String(length=240), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("scenario_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("recommended_scenario_id", sa.Integer(), nullable=True),
        sa.Column("author_claim", sa.String(length=96), nullable=False, server_default="anonymous"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_study_country_id", "study", ["country_id"])


def downgrade() -> None:
    op.drop_index("ix_study_country_id", table_name="study")
    op.drop_table("study")
