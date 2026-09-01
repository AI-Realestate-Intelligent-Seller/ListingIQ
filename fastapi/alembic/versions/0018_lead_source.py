"""Lead source — records where a lead came from (CSV import today; a future
BatchData/PropertyRadar/DealMachine provider integration sets its own value).

Revision ID: 0018_lead_source
Revises: 0017_lead_events
Create Date: 2026-08-31 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0018_lead_source'
down_revision = '0017_lead_events'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {row['name'] for row in inspector.get_columns(table)}


def upgrade():
    if not _has_column('leads', 'source'):
        op.add_column(
            'leads',
            sa.Column('source', sa.String(50), nullable=False, server_default='csv_import'),
        )


def downgrade():
    if _has_column('leads', 'source'):
        op.drop_column('leads', 'source')
