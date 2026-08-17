"""Team invitations table

Revision ID: 0002_invitations
Revises: 0001_initial
Create Date: 2026-08-11 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0002_invitations'
down_revision = '0001_initial'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'invitations',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('role', sa.String(50), nullable=False),
        sa.Column('brokerage_id', sa.String(255), nullable=False),
        sa.Column('brokerage_name', sa.String(255), nullable=True),
        sa.Column('invited_by', sa.Integer, sa.ForeignKey('users.id'), nullable=False),
        # Only a SHA-256 hash of the invitation token is stored, never the token.
        sa.Column('token_hash', sa.String(255), nullable=False),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('accepted_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
    )
    op.create_index('ix_invitations_email', 'invitations', ['email'])
    op.create_index('ix_invitations_brokerage_id', 'invitations', ['brokerage_id'])
    op.create_index('ix_invitations_token_hash', 'invitations', ['token_hash'], unique=True)


def downgrade():
    op.drop_index('ix_invitations_token_hash', table_name='invitations')
    op.drop_index('ix_invitations_brokerage_id', table_name='invitations')
    op.drop_index('ix_invitations_email', table_name='invitations')
    op.drop_table('invitations')
