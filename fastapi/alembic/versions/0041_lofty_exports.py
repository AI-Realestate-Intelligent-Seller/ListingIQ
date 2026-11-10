"""Track each Follow-ups conversation's hand-off to Lofty CRM.

Revision ID: 0041_lofty_exports
Revises: 0040_lead_phone_line_type

Additive only: a new ``lofty_exports`` table, one row per conversation. No
existing table is changed.
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_lofty_exports"
down_revision = "0040_lead_phone_line_type"
branch_labels = None
depends_on = None

TABLE = "lofty_exports"


def upgrade():
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(),
                  sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=True),
        sa.Column("brokerage_id", sa.String(length=255), nullable=True),
        sa.Column("requested_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("connection_ref", sa.String(length=120), nullable=True),
        sa.Column("lofty_lead_id", sa.String(length=40), nullable=True),
        sa.Column("lead_action", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="in_progress"),
        sa.Column("step", sa.String(length=20), nullable=False, server_default="lead"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mute_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("last_message_id", sa.Integer(), nullable=True),
        sa.Column("note_parts", sa.Text(), nullable=True),
        sa.Column("note_ids", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("conversation_id", name="uq_lofty_exports_conversation"),
    )
    op.create_index("ix_lofty_exports_brokerage_id", TABLE, ["brokerage_id"])
    op.create_index("ix_lofty_exports_lofty_lead_id", TABLE, ["lofty_lead_id"])


def downgrade():
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
