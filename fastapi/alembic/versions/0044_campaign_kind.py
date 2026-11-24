"""Mark the reserved 'Individually contacted' campaign bucket.

Revision ID: 0044_campaign_kind
Revises: 0043_abn_experiments
"""

import sqlalchemy as sa
from alembic import op

revision = '0044_campaign_kind'
down_revision = '0043_abn_experiments'
branch_labels = None
depends_on = None

INDEX_NAME = 'ix_campaigns_kind'


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('campaigns')}
    if 'kind' not in columns:
        op.add_column('campaigns', sa.Column('kind', sa.String(length=24), nullable=True))
    inspector = sa.inspect(op.get_bind())
    if not any(index['name'] == INDEX_NAME for index in inspector.get_indexes('campaigns')):
        op.create_index(INDEX_NAME, 'campaigns', ['kind'])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if any(index['name'] == INDEX_NAME for index in inspector.get_indexes('campaigns')):
        op.drop_index(INDEX_NAME, table_name='campaigns')
    columns = {column['name'] for column in inspector.get_columns('campaigns')}
    if 'kind' in columns:
        op.drop_column('campaigns', 'kind')
