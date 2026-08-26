"""One outreach reason for a whole campaign

Blank keeps the previous behaviour: {{reason}} falls back to each lead's own
signal wording, which is what a mixed selection needs.

Revision ID: 0010_campaign_reason
Revises: 0009_campaigns
Create Date: 2026-08-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0010_campaign_reason'
down_revision = '0009_campaigns'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    return column in {row['name'] for row in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    # Checked, for the same reason 0009 is: the app's startup create_all may
    # have built this table before alembic ever reached it.
    if not _has_column('campaigns', 'outreach_reason'):
        op.add_column('campaigns', sa.Column('outreach_reason', sa.String(300), nullable=True))


def downgrade():
    if _has_column('campaigns', 'outreach_reason'):
        op.drop_column('campaigns', 'outreach_reason')
