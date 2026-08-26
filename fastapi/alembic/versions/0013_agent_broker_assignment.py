"""Assign invited agents to an Area Broker

Revision ID: 0013_agent_broker_assignment
Revises: 0012_message_campaign
Create Date: 2026-08-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0013_agent_broker_assignment'
down_revision = '0012_message_campaign'
branch_labels = None
depends_on = None


def _has_column(table, column):
    return column in {item['name'] for item in sa.inspect(op.get_bind()).get_columns(table)}


def _has_index(table, index):
    return index in {item['name'] for item in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    if not _has_column('users', 'assigned_broker_id'):
        # Kept as an application-enforced user id because SQLite cannot add a
        # foreign-key constraint to an existing table without rebuilding it.
        op.add_column('users', sa.Column('assigned_broker_id', sa.Integer(), nullable=True))
    if not _has_index('users', 'ix_users_assigned_broker_id'):
        op.create_index('ix_users_assigned_broker_id', 'users', ['assigned_broker_id'])
    if not _has_column('invitations', 'assigned_broker_id'):
        op.add_column('invitations', sa.Column('assigned_broker_id', sa.Integer(), nullable=True))


def downgrade():
    if _has_column('invitations', 'assigned_broker_id'):
        op.drop_column('invitations', 'assigned_broker_id')
    if _has_column('users', 'assigned_broker_id'):
        op.drop_index('ix_users_assigned_broker_id', table_name='users')
        op.drop_column('users', 'assigned_broker_id')
