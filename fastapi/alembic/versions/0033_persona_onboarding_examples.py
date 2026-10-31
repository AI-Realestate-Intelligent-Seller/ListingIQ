"""Add structured Persona onboarding and versioned conversation examples.

Revision ID: 0033_persona_onboarding_examples
Revises: 0032_persona_core
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_persona_onboarding_examples"
down_revision = "0032_persona_core"
branch_labels = None
depends_on = None


def _example_columns(parent_name: str, parent_table: str):
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(parent_name, sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=False),
        sa.Column("customer_message", sa.Text(), nullable=False),
        sa.Column("preferred_response", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("style_explanation", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(length=30), nullable=False),
        sa.Column("owner_approved", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint([parent_name], [f"{parent_table}.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(parent_name, "position", name=f"uq_{parent_table}_examples_position"),
    ]


def _install_immutable_triggers(dialect: str) -> None:
    for table in ("persona_submission_examples", "persona_version_examples"):
        if dialect == "postgresql":
            function = f"reject_{table}_mutation"
            op.execute(f"""
                CREATE FUNCTION {function}() RETURNS trigger AS $$
                BEGIN RAISE EXCEPTION '{table} rows are immutable'; END;
                $$ LANGUAGE plpgsql
            """)
            op.execute(f"""
                CREATE TRIGGER trg_{table}_immutable
                BEFORE UPDATE OR DELETE ON {table}
                FOR EACH ROW EXECUTE FUNCTION {function}()
            """)
        elif dialect == "sqlite":
            op.execute(f"""
                CREATE TRIGGER trg_{table}_immutable_update BEFORE UPDATE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END
            """)
            op.execute(f"""
                CREATE TRIGGER trg_{table}_immutable_delete BEFORE DELETE ON {table}
                BEGIN SELECT RAISE(ABORT, '{table} rows are immutable'); END
            """)


def _restore_sqlite_version_triggers() -> None:
    """SQLite batch-alter rebuilds the table and drops its Milestone 1 triggers."""
    op.execute("DROP TRIGGER IF EXISTS trg_persona_versions_immutable")
    op.execute("DROP TRIGGER IF EXISTS trg_persona_versions_retain")
    op.execute("""
        CREATE TRIGGER trg_persona_versions_immutable BEFORE UPDATE ON persona_versions
        BEGIN SELECT RAISE(ABORT, 'Published Persona versions are immutable'); END
    """)
    op.execute("""
        CREATE TRIGGER trg_persona_versions_retain BEFORE DELETE ON persona_versions
        BEGIN SELECT RAISE(ABORT, 'Published Persona versions must be retained'); END
    """)


def upgrade():
    dialect = op.get_bind().dialect.name
    with op.batch_alter_table("persona_drafts") as batch:
        batch.add_column(sa.Column("profile_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    with op.batch_alter_table("persona_versions") as batch:
        batch.add_column(sa.Column("profile_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    if dialect == "sqlite":
        _restore_sqlite_version_triggers()

    op.create_table(
        "persona_draft_examples",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("scenario", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("language", sa.String(length=40), nullable=False),
        sa.Column("customer_message", sa.Text(), nullable=False),
        sa.Column("preferred_response", sa.Text(), nullable=False),
        sa.Column("context", sa.Text(), nullable=False),
        sa.Column("style_explanation", sa.Text(), nullable=False),
        sa.Column("provenance", sa.String(length=30), nullable=False),
        sa.Column("owner_approved", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_persona_draft_examples_position"),
        sa.CheckConstraint("channel IN ('sms', 'email')", name="ck_persona_draft_examples_channel"),
        sa.CheckConstraint("provenance IN ('authentic', 'user_written', 'illustrative')", name="ck_persona_draft_examples_provenance"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_persona_draft_examples_persona_id", "persona_draft_examples", ["persona_id"])
    op.create_index("ix_persona_draft_examples_persona_position", "persona_draft_examples", ["persona_id", "position"])
    op.create_index("ix_persona_draft_examples_persona_channel", "persona_draft_examples", ["persona_id", "channel"])

    op.create_table(
        "persona_submission_examples",
        *_example_columns("submission_id", "persona_submissions"),
        sa.Column("source_example_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_persona_submission_examples_submission_id", "persona_submission_examples", ["submission_id"])
    op.create_index("ix_persona_submission_examples_submission", "persona_submission_examples", ["submission_id", "position"])

    op.create_table(
        "persona_version_examples",
        *_example_columns("version_id", "persona_versions"),
        sa.Column("source_submission_example_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_persona_version_examples_version_id", "persona_version_examples", ["version_id"])
    op.create_index("ix_persona_version_examples_version", "persona_version_examples", ["version_id", "position"])
    _install_immutable_triggers(dialect)


def downgrade():
    dialect = op.get_bind().dialect.name
    for table in ("persona_version_examples", "persona_submission_examples"):
        if dialect == "postgresql":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
            op.execute(f"DROP FUNCTION IF EXISTS reject_{table}_mutation()")
        elif dialect == "sqlite":
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_update")
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable_delete")
    op.drop_table("persona_version_examples")
    op.drop_table("persona_submission_examples")
    op.drop_table("persona_draft_examples")
    with op.batch_alter_table("persona_versions") as batch:
        batch.drop_column("profile_config")
    with op.batch_alter_table("persona_drafts") as batch:
        batch.drop_column("profile_config")
    if dialect == "sqlite":
        _restore_sqlite_version_triggers()
