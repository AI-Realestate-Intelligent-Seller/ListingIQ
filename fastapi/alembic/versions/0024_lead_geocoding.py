"""Persist lead coordinates and geocoding queue state.

Revision ID: 0024_lead_geocoding
Revises: 0023_integration_distribution
"""

from alembic import op
import sqlalchemy as sa


revision = '0024_lead_geocoding'
down_revision = '0023_integration_distribution'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('leads', sa.Column('latitude', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column('longitude', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column(
        'geocoding_status', sa.String(24), nullable=False, server_default='pending'))
    op.add_column('leads', sa.Column('geocoding_provider', sa.String(50), nullable=True))
    op.add_column('leads', sa.Column('geocoded_at', sa.DateTime(), nullable=True))
    op.add_column('leads', sa.Column('geocoding_error', sa.String(500), nullable=True))
    op.add_column('leads', sa.Column(
        'geocoding_retry_count', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('leads', sa.Column('normalized_address', sa.String(700), nullable=True))
    op.create_index('ix_leads_geocoding_status', 'leads', ['geocoding_status'])
    op.create_index('ix_leads_coordinates', 'leads', ['latitude', 'longitude'])


def downgrade():
    op.drop_index('ix_leads_coordinates', table_name='leads')
    op.drop_index('ix_leads_geocoding_status', table_name='leads')
    op.drop_column('leads', 'normalized_address')
    op.drop_column('leads', 'geocoding_retry_count')
    op.drop_column('leads', 'geocoding_error')
    op.drop_column('leads', 'geocoded_at')
    op.drop_column('leads', 'geocoding_provider')
    op.drop_column('leads', 'geocoding_status')
    op.drop_column('leads', 'longitude')
    op.drop_column('leads', 'latitude')
