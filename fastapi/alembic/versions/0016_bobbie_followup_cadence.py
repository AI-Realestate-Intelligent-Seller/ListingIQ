"""Persist Bobbie no-response follow-up cadence

Revision ID: 0016_bobbie_followup_cadence
Revises: 0015_assignment_workflow
Create Date: 2026-08-25 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0016_bobbie_followup_cadence'
down_revision = '0015_assignment_workflow'
branch_labels = None
depends_on = None


def upgrade():
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('conversations')}
    if 'followup_attempt_count' not in columns:
        op.add_column('conversations', sa.Column('followup_attempt_count', sa.Integer(), nullable=False, server_default='0'))
    if 'followup_started_at' not in columns:
        op.add_column('conversations', sa.Column('followup_started_at', sa.DateTime(), nullable=True))
    if 'next_followup_at' not in columns:
        op.add_column('conversations', sa.Column('next_followup_at', sa.DateTime(), nullable=True))
        op.create_index('ix_conversations_next_followup_at', 'conversations', ['next_followup_at'])
    if 'final_followup_sent_at' not in columns:
        op.add_column('conversations', sa.Column('final_followup_sent_at', sa.DateTime(), nullable=True))
    if 'dead_at' not in columns:
        op.add_column('conversations', sa.Column('dead_at', sa.DateTime(), nullable=True))


def downgrade():
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('conversations')}
    if 'next_followup_at' in columns:
        op.drop_index('ix_conversations_next_followup_at', table_name='conversations')
    for column in ('dead_at', 'final_followup_sent_at', 'next_followup_at', 'followup_started_at', 'followup_attempt_count'):
        if column in columns:
            op.drop_column('conversations', column)
