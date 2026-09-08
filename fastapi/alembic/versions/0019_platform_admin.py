"""Platform administration tables.

Revision ID: 0019_platform_admin
Revises: 0018_lead_source
"""

from alembic import op
import sqlalchemy as sa

revision = '0019_platform_admin'
down_revision = '0018_lead_source'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if 'feature_flags' not in tables:
        op.create_table(
            'feature_flags',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('key', sa.String(100), nullable=False),
            sa.Column('description', sa.String(500), nullable=True),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('brokerage_id', sa.String(255), nullable=True),
            sa.Column('updated_by', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_feature_flags_key', 'feature_flags', ['key'])
        op.create_index('ix_feature_flags_brokerage_id', 'feature_flags', ['brokerage_id'])
    if 'platform_audit_logs' not in tables:
        op.create_table(
            'platform_audit_logs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('action', sa.String(100), nullable=False),
            sa.Column('target_type', sa.String(50), nullable=False),
            sa.Column('target_id', sa.String(255), nullable=True),
            sa.Column('detail', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_platform_audit_logs_actor_id', 'platform_audit_logs', ['actor_id'])
        op.create_index('ix_platform_audit_logs_action', 'platform_audit_logs', ['action'])
        op.create_index('ix_platform_audit_logs_created_at', 'platform_audit_logs', ['created_at'])


def downgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'platform_audit_logs' in tables:
        op.drop_table('platform_audit_logs')
    if 'feature_flags' in tables:
        op.drop_table('feature_flags')
