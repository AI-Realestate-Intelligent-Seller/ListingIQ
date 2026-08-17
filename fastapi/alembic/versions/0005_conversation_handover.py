"""Broker/Bobbie handover state on a conversation

Revision ID: 0005_conversation_handover
Revises: 0004_calendar_bookings
Create Date: 2026-08-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0005_conversation_handover'
down_revision = '0004_calendar_bookings'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations',
                  sa.Column('handled_by', sa.String(20), nullable=False, server_default='bobbie'))
    # Threads where Bobbie has already stopped belong to the broker from now on.
    op.execute("UPDATE conversations SET handled_by = 'broker' WHERE ai_enabled = 0")


def downgrade():
    op.drop_column('conversations', 'handled_by')
