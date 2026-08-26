"""Campaigns — named outreach batches with a broker-written message template

Existing campaigned leads predate the table, so the upgrade gathers each
broker's already-texted leads into one "Earlier outreach" campaign. Without it
the Campaigns tab would read as empty for every established account, which is
worse than a slightly artificial grouping.

Revision ID: 0009_campaigns
Revises: 0008_followup_state
Create Date: 2026-08-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0009_campaigns'
down_revision = '0008_followup_state'
branch_labels = None
depends_on = None

LEGACY_NAME = 'Earlier outreach'


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in {row['name'] for row in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {row['name'] for row in _inspector().get_indexes(table)}


def upgrade():
    # The app calls Base.metadata.create_all() on startup, so a database that
    # has run the new code before this migration already carries an empty
    # `campaigns` table — and no campaign_id columns, because create_all never
    # alters an existing table. Each step is therefore checked rather than
    # assumed, so the migration lands the same way on a fresh database and on
    # one the application has already touched.
    if not _has_table('campaigns'):
        op.create_table(
            'campaigns',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('name', sa.String(200), nullable=False, server_default=''),
            sa.Column('message_template', sa.Text(), nullable=False, server_default=''),
            sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
    if not _has_index('campaigns', 'ix_campaigns_user_id'):
        op.create_index('ix_campaigns_user_id', 'campaigns', ['user_id'])

    if not _has_column('leads', 'campaign_id'):
        op.add_column('leads', sa.Column('campaign_id', sa.Integer(), nullable=True))
    if not _has_index('leads', 'ix_leads_campaign_id'):
        op.create_index('ix_leads_campaign_id', 'leads', ['campaign_id'])
    if not _has_column('conversations', 'campaign_id'):
        op.add_column('conversations', sa.Column('campaign_id', sa.Integer(), nullable=True))
    if not _has_index('conversations', 'ix_conversations_campaign_id'):
        op.create_index('ix_conversations_campaign_id', 'conversations', ['campaign_id'])

    _backfill_legacy_campaigns()


def _backfill_legacy_campaigns():
    """One campaign per broker, holding every lead already handed to Bobbie."""
    connection = op.get_bind()
    owners = connection.execute(sa.text(
        'SELECT DISTINCT user_id FROM leads WHERE conversation_id IS NOT NULL'
    )).fetchall()

    for (user_id,) in owners:
        existing = connection.execute(sa.text(
            'SELECT id FROM campaigns WHERE user_id = :user_id AND name = :name'
        ), {'user_id': user_id, 'name': LEGACY_NAME}).scalar()
        if existing is not None:
            continue

        # created_at doubles as sent_at: these leads were texted, not drafted.
        sent_at = connection.execute(sa.text(
            'SELECT MIN(created_at) FROM leads '
            'WHERE user_id = :user_id AND conversation_id IS NOT NULL'
        ), {'user_id': user_id}).scalar()

        connection.execute(sa.text(
            'INSERT INTO campaigns (user_id, name, message_template, status, sent_at, created_at) '
            'VALUES (:user_id, :name, :template, :status, :sent_at, :created_at)'
        ), {
            'user_id': user_id,
            'name': LEGACY_NAME,
            # Blank rather than the current default: these went out under the
            # old hard-coded opener, and guessing the wording would be a lie.
            'template': '',
            'status': 'sent',
            'sent_at': sent_at,
            'created_at': sent_at,
        })
        campaign_id = connection.execute(sa.text(
            'SELECT id FROM campaigns WHERE user_id = :user_id AND name = :name'
        ), {'user_id': user_id, 'name': LEGACY_NAME}).scalar()

        connection.execute(sa.text(
            'UPDATE leads SET campaign_id = :campaign_id '
            'WHERE user_id = :user_id AND conversation_id IS NOT NULL'
        ), {'campaign_id': campaign_id, 'user_id': user_id})
        connection.execute(sa.text(
            'UPDATE conversations SET campaign_id = :campaign_id WHERE id IN ('
            '  SELECT conversation_id FROM leads '
            '  WHERE user_id = :user_id AND conversation_id IS NOT NULL)'
        ), {'campaign_id': campaign_id, 'user_id': user_id})


def downgrade():
    if _has_index('conversations', 'ix_conversations_campaign_id'):
        op.drop_index('ix_conversations_campaign_id', table_name='conversations')
    if _has_column('conversations', 'campaign_id'):
        op.drop_column('conversations', 'campaign_id')
    if _has_index('leads', 'ix_leads_campaign_id'):
        op.drop_index('ix_leads_campaign_id', table_name='leads')
    if _has_column('leads', 'campaign_id'):
        op.drop_column('leads', 'campaign_id')
    if _has_table('campaigns'):
        op.drop_table('campaigns')
