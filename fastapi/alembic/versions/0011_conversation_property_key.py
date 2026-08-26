"""Normalised property key on a conversation

The cross-brokerage claim is on the property, so it needs a comparable form of
the address. Existing rows are backfilled through the same normaliser the
application uses, so old threads take part in the claim too.

Revision ID: 0011_conversation_property_key
Revises: 0010_campaign_reason
Create Date: 2026-08-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0011_conversation_property_key'
down_revision = '0010_campaign_reason'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {row['name'] for row in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {row['name'] for row in _inspector().get_indexes(table)}


def upgrade():
    # Checked, like 0009 and 0010: the app's startup create_all may have built
    # this column already on an environment that ran the new code first.
    if not _has_column('conversations', 'property_key'):
        op.add_column('conversations', sa.Column('property_key', sa.String(500), nullable=True))
    if not _has_index('conversations', 'ix_conversations_property_key'):
        op.create_index('ix_conversations_property_key', 'conversations', ['property_key'])

    # Imported here, not at module scope: alembic loads every revision file on
    # startup and this one should not drag the app package in until it runs.
    from app.leads.address import canonical

    connection = op.get_bind()
    rows = connection.execute(sa.text(
        'SELECT id, property_address FROM conversations WHERE property_address IS NOT NULL'
    )).fetchall()
    for conversation_id, address in rows:
        key = canonical(address)
        if key:
            connection.execute(
                sa.text('UPDATE conversations SET property_key = :key WHERE id = :id'),
                {'key': key, 'id': conversation_id})


def downgrade():
    if _has_index('conversations', 'ix_conversations_property_key'):
        op.drop_index('ix_conversations_property_key', table_name='conversations')
    if _has_column('conversations', 'property_key'):
        op.drop_column('conversations', 'property_key')
