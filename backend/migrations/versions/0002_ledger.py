"""The audit trail becomes a ledger: who claims a change, in which sitting, and whether
it still stands.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("audit_entry", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("author_claim", sa.String(length=96), nullable=False, server_default="anonymous")
        )
        batch_op.add_column(sa.Column("batch_id", sa.String(length=36), nullable=True))
        batch_op.add_column(
            sa.Column("status", sa.String(length=12), nullable=False, server_default="applied")
        )
        batch_op.add_column(sa.Column("reverts_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_audit_entry_batch_id"), ["batch_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_audit_entry_reverts_id", "audit_entry", ["reverts_id"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_entry", schema=None) as batch_op:
        batch_op.drop_constraint("fk_audit_entry_reverts_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_audit_entry_batch_id"))
        batch_op.drop_column("reverts_id")
        batch_op.drop_column("status")
        batch_op.drop_column("batch_id")
        batch_op.drop_column("author_claim")
