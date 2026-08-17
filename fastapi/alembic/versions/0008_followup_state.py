"""Follow-ups decision recorded on a conversation

Revision ID: 0008_followup_state
Revises: 0007_lead_reason_details
Create Date: 2026-08-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0008_followup_state'
down_revision = '0007_lead_reason_details'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversations',
                  sa.Column('followup_state', sa.String(20), nullable=False, server_default='pending'))


def downgrade():
    op.drop_column('conversations', 'followup_state')
