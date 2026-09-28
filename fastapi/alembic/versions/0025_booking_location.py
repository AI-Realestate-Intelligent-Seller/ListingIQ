"""Store the address for agent-scheduled in-person appointments.

Revision ID: 0025_booking_location
Revises: 0024_lead_geocoding
"""

from alembic import op
import sqlalchemy as sa


revision = '0025_booking_location'
down_revision = '0024_lead_geocoding'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bookings', sa.Column('location_address', sa.String(500), nullable=True))


def downgrade():
    op.drop_column('bookings', 'location_address')
