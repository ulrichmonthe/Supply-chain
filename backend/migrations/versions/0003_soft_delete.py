"""Nothing is deleted by an import any more: rows are retired, and each one remembers
what the last import set so a later one can tell an update from a conflict.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TABLES = ("node", "edge", "product", "demand")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(
                sa.Column("retired_reason", sa.String(length=255), nullable=False, server_default="")
            )
            batch_op.add_column(sa.Column("last_import", sa.JSON(), nullable=False, server_default="{}"))
            batch_op.create_index(batch_op.f(f"ix_{table}_retired_at"), ["retired_at"], unique=False)


def downgrade() -> None:
    for table in reversed(TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_index(batch_op.f(f"ix_{table}_retired_at"))
            batch_op.drop_column("last_import")
            batch_op.drop_column("retired_reason")
            batch_op.drop_column("retired_at")
