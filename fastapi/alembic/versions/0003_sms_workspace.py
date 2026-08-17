"""SMS workspace: Bobbie autopilot and lead tracking fields

Revision ID: 0003_sms_workspace
Revises: 0002_invitations
Create Date: 2026-08-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0003_sms_workspace'
down_revision = '0002_invitations'
branch_labels = None
depends_on = None

CONVERSATION_COLUMNS = (
    sa.Column('ai_enabled', sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column('recipient_ai_enabled', sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column('lead_context', sa.Text, nullable=True),
    sa.Column('lead_status', sa.String(50), nullable=False, server_default='processing'),
    sa.Column('queue_status', sa.String(50), nullable=False, server_default='idle'),
    sa.Column('dnc_alert', sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column('meeting_booked', sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column('processed_at', sa.DateTime, nullable=True),
)


def upgrade():
    for column in CONVERSATION_COLUMNS:
        op.add_column('conversations', column)
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
    op.add_column('messages', sa.Column('telnyx_id', sa.String(128), nullable=True))
    op.create_index('ix_messages_telnyx_id', 'messages', ['telnyx_id'])
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])


def downgrade():
    op.drop_index('ix_messages_conversation_id', table_name='messages')
    op.drop_index('ix_messages_telnyx_id', table_name='messages')
    op.drop_column('messages', 'telnyx_id')
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    for column in reversed(CONVERSATION_COLUMNS):
        op.drop_column('conversations', column.name)
