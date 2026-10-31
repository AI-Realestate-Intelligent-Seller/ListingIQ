"""Record how a Persona import is interpreted: locally (default) or by AI on request.

Revision ID: 0036_persona_import_mode
Revises: 0035_persona_example_flags

Existing jobs become "local", so retrying them never calls an AI provider
unless the owner explicitly asks for AI-assisted interpretation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0036_persona_import_mode"
down_revision = "0035_persona_example_flags"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "persona_import_jobs",
        sa.Column("extraction_mode", sa.String(length=20), nullable=False, server_default=sa.text("'local'")),
    )


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("persona_import_jobs") as batch:
            batch.drop_column("extraction_mode")
    else:
        op.drop_column("persona_import_jobs", "extraction_mode")
