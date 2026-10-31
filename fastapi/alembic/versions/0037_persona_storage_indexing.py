"""Persona source object storage metadata and durable knowledge-indexing jobs.

Revision ID: 0037_persona_storage_indexing
Revises: 0036_persona_import_mode

Additive only:
- ``persona_import_jobs.storage_backend`` / ``storage_key`` record where the
  original upload lives. Existing rows become ``database`` and keep reading
  their ``document_bytes``; no stored bytes are touched or deleted.
- ``persona_import_jobs.document_bytes`` becomes nullable so an object-storage
  (R2) upload does not have to duplicate the file into PostgreSQL. Existing
  values are preserved.
- ``persona_knowledge_index_jobs`` is the outbox that publication writes in its
  own transaction; a background worker embeds and upserts to the vector store.
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_persona_storage_indexing"
down_revision = "0036_persona_import_mode"
branch_labels = None
depends_on = None


def upgrade():
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        with op.batch_alter_table("persona_import_jobs") as batch:
            batch.add_column(sa.Column("storage_backend", sa.String(length=20), nullable=False,
                                       server_default=sa.text("'database'")))
            batch.add_column(sa.Column("storage_key", sa.String(length=900), nullable=True))
            batch.alter_column("document_bytes", existing_type=sa.LargeBinary(), nullable=True)
    else:
        op.add_column("persona_import_jobs", sa.Column(
            "storage_backend", sa.String(length=20), nullable=False, server_default=sa.text("'database'")))
        op.add_column("persona_import_jobs", sa.Column("storage_key", sa.String(length=900), nullable=True))
        op.alter_column("persona_import_jobs", "document_bytes", existing_type=sa.LargeBinary(), nullable=True)

    op.create_table(
        "persona_knowledge_index_jobs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("version_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("brokerage_id", sa.String(length=255), nullable=False),
        sa.Column("source_document_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'indexing', 'ready', 'failed')",
                           name="ck_persona_knowledge_index_jobs_status"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["version_id"], ["persona_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("version_id", name="uq_persona_knowledge_index_jobs_version"),
    )
    for name, columns in (
        ("ix_persona_knowledge_index_jobs_persona_id", ["persona_id"]),
        ("ix_persona_knowledge_index_jobs_version_id", ["version_id"]),
        ("ix_persona_knowledge_index_jobs_owner_user_id", ["owner_user_id"]),
        ("ix_persona_knowledge_index_jobs_brokerage_id", ["brokerage_id"]),
        ("ix_persona_knowledge_index_jobs_status", ["status"]),
        ("ix_persona_knowledge_index_jobs_status_next", ["status", "next_attempt_at"]),
        ("ix_persona_knowledge_index_jobs_owner", ["owner_user_id", "brokerage_id"]),
    ):
        op.create_index(name, "persona_knowledge_index_jobs", columns)


def downgrade():
    # document_bytes stays nullable on downgrade: rows stored only in object
    # storage have no database copy, and restoring NOT NULL would fail or
    # require discarding them.
    op.drop_table("persona_knowledge_index_jobs")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("persona_import_jobs") as batch:
            batch.drop_column("storage_key")
            batch.drop_column("storage_backend")
    else:
        op.drop_column("persona_import_jobs", "storage_key")
        op.drop_column("persona_import_jobs", "storage_backend")
