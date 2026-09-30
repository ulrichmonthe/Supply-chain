"""Data onboarding: provenance classes on demand, the country pack, staging, the
facility crosswalk, the review queue and sign-off.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("demand", schema=None) as batch_op:
        batch_op.add_column(sa.Column("provenance_class", sa.String(length=16), nullable=False, server_default="illustrative"))

    op.create_table(
        "country_pack",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("pack", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("author_claim", sa.String(length=96), nullable=False),
        sa.Column("approved_by", sa.String(length=96), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("country_id", "version", name="uq_country_pack_version"),
    )
    op.create_index("ix_country_pack_country_id", "country_pack", ["country_id"])

    op.create_table(
        "onboarding_run",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("pack_id", sa.Integer(), sa.ForeignKey("country_pack.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("preparer", sa.String(length=96), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_onboarding_run_country_id", "onboarding_run", ["country_id"])

    op.create_table(
        "source_file",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("onboarding_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("uploader", sa.String(length=96), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("vintage_from", sa.String(length=16), nullable=False),
        sa.Column("vintage_to", sa.String(length=16), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("mapping", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
    )
    op.create_index("ix_source_file_run_id", "source_file", ["run_id"])
    op.create_index("ix_source_file_sha256", "source_file", ["sha256"])

    op.create_table(
        "staged_record",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("onboarding_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("aux", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "domain", "key", name="uq_staged_run_domain_key"),
    )
    op.create_index("ix_staged_record_run_id", "staged_record", ["run_id"])

    op.create_table(
        "facility_match",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("onboarding_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_file_id", sa.Integer(), sa.ForeignKey("source_file.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_key", sa.String(length=160), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_record", sa.JSON(), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("canonical_code", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("decided_by", sa.String(length=96), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_facility_match_run_id", "facility_match", ["run_id"])

    op.create_table(
        "crosswalk",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country_id", sa.Integer(), sa.ForeignKey("country.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("admin1", sa.String(length=128), nullable=True),
        sa.Column("admin2", sa.String(length=128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("country_id", "canonical_code", name="uq_crosswalk_country_code"),
    )
    op.create_index("ix_crosswalk_country_id", "crosswalk", ["country_id"])

    op.create_table(
        "review_item",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("onboarding_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("impact", sa.Float(), nullable=False),
        sa.Column("proposed_by", sa.String(length=16), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("choice", sa.String(length=200), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("decided_by", sa.String(length=96), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_review_item_run_id", "review_item", ["run_id"])

    op.create_table(
        "approval",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("onboarding_run.id", ondelete="CASCADE"), nullable=False),
        sa.Column("approver", sa.String(length=96), nullable=False),
        sa.Column("decision", sa.String(length=12), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_approval_run_id", "approval", ["run_id"])


def downgrade() -> None:
    for table in ("approval", "review_item", "crosswalk", "facility_match", "staged_record", "source_file", "onboarding_run", "country_pack"):
        op.drop_table(table)
    with op.batch_alter_table("demand", schema=None) as batch_op:
        batch_op.drop_column("provenance_class")
