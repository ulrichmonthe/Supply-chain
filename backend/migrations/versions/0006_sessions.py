"""Sessions: a named, complete save of a country's working state, and the snapshot
table that holds it content-addressed.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dataset_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_dataset_snapshot_country_id", "dataset_snapshot", ["country_id"])
    op.create_index("ix_dataset_snapshot_sha256", "dataset_snapshot", ["sha256"])

    op.create_table(
        "work_session",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(length=12), nullable=False, server_default="saved"),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("work_session.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "snapshot_id", sa.Integer(), sa.ForeignKey("dataset_snapshot.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ledger_position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("author_claim", sa.String(length=96), nullable=False, server_default="anonymous"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_work_session_country_id", "work_session", ["country_id"])

    with op.batch_alter_table("country", schema=None) as batch_op:
        batch_op.add_column(sa.Column("current_session_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("current_session_position", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("country", schema=None) as batch_op:
        batch_op.drop_column("current_session_position")
        batch_op.drop_column("current_session_id")
    op.drop_index("ix_work_session_country_id", table_name="work_session")
    op.drop_table("work_session")
    op.drop_index("ix_dataset_snapshot_sha256", table_name="dataset_snapshot")
    op.drop_index("ix_dataset_snapshot_country_id", table_name="dataset_snapshot")
    op.drop_table("dataset_snapshot")
