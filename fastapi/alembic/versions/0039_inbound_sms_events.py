"""Record each inbound Telnyx message once so redelivered webhooks are ignored.

Revision ID: 0039_inbound_sms_events
Revises: 0038_notification_reminder_nullable

Additive only: a new ``inbound_sms_events`` table with a unique key on
(provider_message_id, from_number). ``messages`` is not changed, so existing
rows, including historical duplicate inbound messages, stay as they are and
cannot block the new constraint.
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_inbound_sms_events"
down_revision = "0038_notification_reminder_nullable"
branch_labels = None
depends_on = None

TABLE = "inbound_sms_events"


def upgrade():
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider_message_id", sa.String(length=128), nullable=False),
        sa.Column("from_number", sa.String(length=50), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "provider_message_id",
            "from_number",
            name="uq_inbound_sms_events_provider_from",
        ),
    )


def downgrade():
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
