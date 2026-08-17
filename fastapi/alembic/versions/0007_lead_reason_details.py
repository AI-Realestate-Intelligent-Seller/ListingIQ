"""Per-lead outreach reason and imported property attributes

Revision ID: 0007_lead_reason_details
Revises: 0006_lead_pool
Create Date: 2026-08-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0007_lead_reason_details'
down_revision = '0006_lead_pool'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('leads', sa.Column('outreach_reason', sa.String(300), nullable=True))
    op.add_column('leads', sa.Column('details', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('leads', 'details')
    op.drop_column('leads', 'outreach_reason')
