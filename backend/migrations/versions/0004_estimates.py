"""Estimates that stay live: a demand row or a storage capacity can carry the rule that
produced it, and be recomputed when its inputs change until a person types over it.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("demand", schema=None) as batch_op:
        batch_op.add_column(sa.Column("derivation", sa.JSON(), nullable=True))
    with op.batch_alter_table("node", schema=None) as batch_op:
        batch_op.add_column(sa.Column("derivations", sa.JSON(), nullable=False, server_default="{}"))


def downgrade() -> None:
    with op.batch_alter_table("node", schema=None) as batch_op:
        batch_op.drop_column("derivations")
    with op.batch_alter_table("demand", schema=None) as batch_op:
        batch_op.drop_column("derivation")
