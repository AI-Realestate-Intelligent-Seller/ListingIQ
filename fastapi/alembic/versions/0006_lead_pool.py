"""Lead pool — imported prospects that become Bobbie campaigns

Revision ID: 0006_lead_pool
Revises: 0005_conversation_handover
Create Date: 2026-08-12 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0006_lead_pool'
down_revision = '0005_conversation_handover'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'leads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('owner_name', sa.String(255)),
        sa.Column('phone', sa.String(50)),
        sa.Column('property_address', sa.String(500)),
        sa.Column('area', sa.String(120)),
        sa.Column('signals', sa.String(500), nullable=False, server_default=''),
        sa.Column('score', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('dnc', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(), nullable=True),
        sa.Column('refreshed_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
    )
    op.create_index('ix_leads_user_id', 'leads', ['user_id'])
    op.create_index('ix_leads_phone', 'leads', ['phone'])
    op.create_index('ix_leads_conversation_id', 'leads', ['conversation_id'])


def downgrade():
    op.drop_index('ix_leads_conversation_id', table_name='leads')
    op.drop_index('ix_leads_phone', table_name='leads')
    op.drop_index('ix_leads_user_id', table_name='leads')
    op.drop_table('leads')
