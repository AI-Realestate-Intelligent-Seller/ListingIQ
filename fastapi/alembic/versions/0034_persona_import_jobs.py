"""Add durable Persona document-processing jobs.

Revision ID: 0034_persona_import_jobs
Revises: 0033_persona_onboarding_examples
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_persona_import_jobs"
down_revision = "0033_persona_onboarding_examples"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "persona_import_jobs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("brokerage_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("base_revision", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("document_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("document_text", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("current_stage", sa.String(length=50), nullable=True),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("extraction", sa.JSON(), nullable=True),
        sa.Column("proposal", sa.JSON(), nullable=True),
        sa.Column("assessment", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("retryable", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("applied_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('queued', 'processing', 'completed', 'failed', 'applied')", name="ck_persona_import_jobs_status"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("persona_id", "file_hash", name="uq_persona_import_jobs_file_hash"),
    )
    for name, columns in (
        ("ix_persona_import_jobs_persona_id", ["persona_id"]),
        ("ix_persona_import_jobs_brokerage_id", ["brokerage_id"]),
        ("ix_persona_import_jobs_owner_user_id", ["owner_user_id"]),
        ("ix_persona_import_jobs_status", ["status"]),
        ("ix_persona_import_jobs_owner_created", ["owner_user_id", "created_at"]),
        ("ix_persona_import_jobs_status_updated", ["status", "updated_at"]),
    ):
        op.create_index(name, "persona_import_jobs", columns)


def downgrade():
    op.drop_table("persona_import_jobs")
