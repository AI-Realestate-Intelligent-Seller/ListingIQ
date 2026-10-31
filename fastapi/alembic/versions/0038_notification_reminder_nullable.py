"""Make notifications.reminder_id nullable.

Revision ID: 0038_notification_reminder_nullable
Revises: 0037_persona_storage_indexing

The model and the original migration (ca392df3bef1) declare
``notifications.reminder_id`` nullable: only booking reminders set it, and
CUSTOMER_REPLY notifications leave it empty. Some existing databases have the
column as NOT NULL, so inserting a CUSTOMER_REPLY notification failed.

This only drops the NOT NULL. It does nothing when the column is already
nullable. Rows, indexes, the unique constraint and foreign keys are kept.
SQLite rebuilds the table through batch mode, which copies every row.

This revision ID is 35 characters, but Alembic creates
``alembic_version.version_num`` as VARCHAR(32). On PostgreSQL the column is
widened first, in the same transaction, so recording this revision succeeds.
SQLite does not enforce VARCHAR lengths and needs no change.
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_notification_reminder_nullable"
down_revision = "0037_persona_storage_indexing"
branch_labels = None
depends_on = None

TABLE = "notifications"
COLUMN = "reminder_id"
VERSION_NUM_LENGTH = 128


def _widen_alembic_version(bind) -> None:
    if bind.dialect.name == "sqlite":
        return
    length = bind.execute(sa.text(
        "SELECT character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'alembic_version' "
        "AND column_name = 'version_num'"
    )).scalar()
    if length is not None and length < VERSION_NUM_LENGTH:
        op.alter_column(
            "alembic_version",
            "version_num",
            existing_type=sa.String(length),
            type_=sa.String(VERSION_NUM_LENGTH),
            existing_nullable=False,
        )


def _reminder_column_nullable(bind) -> bool | None:
    inspector = sa.inspect(bind)
    if TABLE not in inspector.get_table_names():
        return None
    for column in inspector.get_columns(TABLE):
        if column["name"] == COLUMN:
            return bool(column["nullable"])
    return None


def upgrade():
    bind = op.get_bind()
    # Must run before this revision's ID is written to alembic_version.
    _widen_alembic_version(bind)
    nullable = _reminder_column_nullable(bind)
    if nullable is None or nullable:
        return  # table/column missing, or already nullable: nothing to do

    if bind.dialect.name == "sqlite":
        # SQLite cannot drop NOT NULL in place; batch mode copies the table.
        with op.batch_alter_table(TABLE) as batch:
            batch.alter_column(COLUMN, existing_type=sa.Integer(), nullable=True)
    else:
        op.alter_column(TABLE, COLUMN, existing_type=sa.Integer(), nullable=True)


def downgrade():
    # alembic_version stays widened: narrowing it could fail on a longer ID.
    # The intended schema has always been nullable, and rows without a reminder
    # (CUSTOMER_REPLY) would block a NOT NULL constraint, so this is a no-op.
    pass
