"""Keep import review flags on Persona examples through submission and publication.

Revision ID: 0035_persona_example_flags
Revises: 0034_persona_import_jobs

The column is added with a constant default, which both PostgreSQL and SQLite
apply without rewriting or rebuilding the table, so the immutability triggers on
the submission and version example tables stay in place and existing rows read
as "no flags".
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_persona_example_flags"
down_revision = "0034_persona_import_jobs"
branch_labels = None
depends_on = None

TABLES = ("persona_draft_examples", "persona_submission_examples", "persona_version_examples")
IMMUTABLE_TABLES = ("persona_submission_examples", "persona_version_examples")


def upgrade():
    for table in TABLES:
        op.add_column(
            table,
            sa.Column("review_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )


def _restore_sqlite_triggers(table: str) -> None:
    # SQLite drops a column by rebuilding the table, which removes its triggers.
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_update")
    op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_delete")
    op.execute(f"""
        CREATE TRIGGER trg_{table}_immutable_update BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END
    """)
    op.execute(f"""
        CREATE TRIGGER trg_{table}_immutable_delete BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END
    """)


def downgrade():
    dialect = op.get_bind().dialect.name
    for table in TABLES:
        if dialect == "sqlite":
            with op.batch_alter_table(table) as batch:
                batch.drop_column("review_flags")
            if table in IMMUTABLE_TABLES:
                _restore_sqlite_triggers(table)
        else:
            op.drop_column(table, "review_flags")
