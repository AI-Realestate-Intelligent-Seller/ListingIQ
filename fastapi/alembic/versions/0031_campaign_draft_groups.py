"""Main-signal campaign draft groups.

The migration is intentionally schema-only. Historical assignment can touch
many leads and is therefore left to a separately approved bounded operation.
"""

from alembic import op
import sqlalchemy as sa


revision = '0031_campaign_draft_groups'
down_revision = '0030_outbound_sms_jobs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('leads', sa.Column('main_signal', sa.String(length=64), nullable=True))
    op.add_column('leads', sa.Column('main_signal_basis', sa.String(length=24), nullable=True))
    op.add_column('campaigns', sa.Column('reason_groups', sa.Text(), nullable=True))
    op.create_index(
        'ix_leads_campaign_main_signal',
        'leads',
        ['campaign_id', 'main_signal'],
    )


def downgrade():
    op.drop_index('ix_leads_campaign_main_signal', table_name='leads')
    op.drop_column('campaigns', 'reason_groups')
    op.drop_column('leads', 'main_signal_basis')
    op.drop_column('leads', 'main_signal')
