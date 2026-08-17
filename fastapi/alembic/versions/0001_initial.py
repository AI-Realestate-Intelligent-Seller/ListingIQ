"""Initial database schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-08-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('email', sa.String(255), unique=True, index=True, nullable=False),
        sa.Column('hashed_password', sa.String(255), nullable=False),
        sa.Column('full_name', sa.String(255), nullable=True),
        sa.Column('first_name', sa.String(255), nullable=True),
        sa.Column('last_name', sa.String(255), nullable=True),
        sa.Column('brokerage_name', sa.String(255), nullable=True),
        sa.Column('brokerage_id', sa.String(255), nullable=True),
        sa.Column('is_head_or_owner', sa.Boolean, nullable=True, server_default=sa.false()),
        sa.Column('is_verified', sa.Boolean, nullable=True, server_default=sa.false()),
        sa.Column('is_active', sa.Boolean, nullable=True, server_default=sa.true()),
        sa.Column('role', sa.String(50), nullable=True, server_default='user'),
        sa.Column('google_refresh_token', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('contact', sa.String(50), index=True, nullable=False),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('property_address', sa.String(500), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('conversation_id', sa.Integer, sa.ForeignKey('conversations.id'), nullable=True),
        sa.Column('direction', sa.String(20), nullable=True),
        sa.Column('from_number', sa.String(50), nullable=True),
        sa.Column('to_number', sa.String(50), nullable=True),
        sa.Column('text', sa.Text, nullable=True),
        sa.Column('status', sa.String(50), nullable=True),
        sa.Column('event_type', sa.String(100), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'bookings',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('title', sa.String(255), nullable=True),
        sa.Column('start_at', sa.DateTime, nullable=True),
        sa.Column('end_at', sa.DateTime, nullable=True),
        sa.Column('join_token', sa.String(128), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )

    op.create_table(
        'ai_runs',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=True),
        sa.Column('model', sa.String(255), nullable=True),
        sa.Column('prompt', sa.Text, nullable=True),
        sa.Column('result', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )


def downgrade():
    op.drop_table('ai_runs')
    op.drop_table('bookings')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('users')
