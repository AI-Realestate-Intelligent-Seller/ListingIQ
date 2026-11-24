"""Persist A/B/n experiments and link their campaign variants.

Revision ID: 0043_abn_experiments
Revises: 0042_hot_path_indexes
"""

import sqlalchemy as sa
from alembic import op

revision = '0043_abn_experiments'
down_revision = '0042_hot_path_indexes'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'experiments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('goal_notes', sa.Text()),
        sa.Column('test_dimension', sa.String(40), nullable=False),
        sa.Column('test_dimension_other', sa.String(200)),
        sa.Column('winner_metric', sa.String(40), nullable=False),
        sa.Column('allocation_mode', sa.String(20), nullable=False, server_default='balanced'),
        sa.Column('timezone', sa.String(100), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False, server_default='scheduled'),
        sa.Column('variant_config', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('campaign_ids', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('total_leads', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('expected_max_cost', sa.Float(), nullable=False, server_default='0'),
        sa.Column('error', sa.Text()),
        sa.Column('launched_at', sa.DateTime()),
        sa.Column('cancelled_at', sa.DateTime()),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_experiments_user_id', 'experiments', ['user_id'])
    op.create_index('ix_experiments_scheduled_at', 'experiments', ['scheduled_at'])
    op.create_index('ix_experiments_status', 'experiments', ['status'])
    op.add_column('campaigns', sa.Column('experiment_id', sa.Integer(), nullable=True))
    op.add_column('campaigns', sa.Column('experiment_variant', sa.String(80), nullable=True))
    op.add_column('campaigns', sa.Column('followup_enabled', sa.Boolean(), nullable=True))
    op.add_column('campaigns', sa.Column('followup_template', sa.Text(), nullable=True))
    op.add_column('campaigns', sa.Column('followup_delay_hours', sa.Float(), nullable=True))
    # SQLite cannot ALTER a constraint onto a table that already exists, and it
    # does not enforce foreign keys unless asked to. The column and its index
    # are what the application reads, so the key is added where it is enforced.
    if op.get_bind().dialect.name != 'sqlite':
        op.create_foreign_key('fk_campaigns_experiment_id', 'campaigns', 'experiments',
                              ['experiment_id'], ['id'])
    op.create_index('ix_campaigns_experiment_id', 'campaigns', ['experiment_id'])


def downgrade():
    op.drop_index('ix_campaigns_experiment_id', table_name='campaigns')
    if op.get_bind().dialect.name != 'sqlite':
        op.drop_constraint('fk_campaigns_experiment_id', 'campaigns', type_='foreignkey')
    for column in ('followup_delay_hours', 'followup_template', 'followup_enabled', 'experiment_variant', 'experiment_id'):
        op.drop_column('campaigns', column)
    op.drop_index('ix_experiments_status', table_name='experiments')
    op.drop_index('ix_experiments_scheduled_at', table_name='experiments')
    op.drop_index('ix_experiments_user_id', table_name='experiments')
    op.drop_table('experiments')
