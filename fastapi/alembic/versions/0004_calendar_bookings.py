"""Move the meeting calendar in-process: contact name + lookup indexes

Revision ID: 0004_calendar_bookings
Revises: 0003_sms_workspace
Create Date: 2026-08-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0004_calendar_bookings'
down_revision = '0003_sms_workspace'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bookings', sa.Column('name', sa.String(255), nullable=True))
    op.create_index('ix_bookings_user_id', 'bookings', ['user_id'])
    op.create_index('ix_bookings_join_token', 'bookings', ['join_token'], unique=True)
    op.create_index('ix_bookings_start_at', 'bookings', ['start_at'])


def downgrade():
    op.drop_index('ix_bookings_start_at', table_name='bookings')
    op.drop_index('ix_bookings_join_token', table_name='bookings')
    op.drop_index('ix_bookings_user_id', table_name='bookings')
    op.drop_column('bookings', 'name')
