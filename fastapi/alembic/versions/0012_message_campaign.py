"""Attribute an outreach message to its campaign and its property

A conversation can carry outreach from two campaigns when one owner has two
properties, so conversations.campaign_id (the campaign that opened the thread)
cannot answer "how many did this campaign deliver". The message can.

Revision ID: 0012_message_campaign
Revises: 0011_conversation_property_key
Create Date: 2026-08-18 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '0012_message_campaign'
down_revision = '0011_conversation_property_key'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return column in {row['name'] for row in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {row['name'] for row in _inspector().get_indexes(table)}


def upgrade():
    if not _has_column('messages', 'campaign_id'):
        op.add_column('messages', sa.Column('campaign_id', sa.Integer(), nullable=True))
    if not _has_index('messages', 'ix_messages_campaign_id'):
        op.create_index('ix_messages_campaign_id', 'messages', ['campaign_id'])
    if not _has_column('messages', 'property_key'):
        op.add_column('messages', sa.Column('property_key', sa.String(500), nullable=True))
    if not _has_index('messages', 'ix_messages_property_key'):
        op.create_index('ix_messages_property_key', 'messages', ['property_key'])

    # Existing outreach predates per-message attribution, but its thread only
    # ever belonged to one campaign back then, so the thread's campaign is the
    # right answer for every row already in the table.
    from app.leads.address import canonical

    connection = op.get_bind()
    # Every outreach already sent was about its thread's property, since a
    # thread could only cover one before this revision.
    for message_id, address in connection.execute(sa.text(
            'SELECT m.id, c.property_address FROM messages m '
            'JOIN conversations c ON c.id = m.conversation_id '
            "WHERE m.event_type = 'outreach.initial' AND m.property_key IS NULL"
    )).fetchall():
        key = canonical(address)
        if key:
            connection.execute(
                sa.text('UPDATE messages SET property_key = :key WHERE id = :id'),
                {'key': key, 'id': message_id})

    connection.execute(sa.text(
        'UPDATE messages SET campaign_id = ('
        '  SELECT c.campaign_id FROM conversations c WHERE c.id = messages.conversation_id)'
        " WHERE direction = 'outbound' AND event_type = 'outreach.initial'"
        '   AND campaign_id IS NULL'
    ))


def downgrade():
    if _has_index('messages', 'ix_messages_property_key'):
        op.drop_index('ix_messages_property_key', table_name='messages')
    if _has_column('messages', 'property_key'):
        op.drop_column('messages', 'property_key')
    if _has_index('messages', 'ix_messages_campaign_id'):
        op.drop_index('ix_messages_campaign_id', table_name='messages')
    if _has_column('messages', 'campaign_id'):
        op.drop_column('messages', 'campaign_id')
