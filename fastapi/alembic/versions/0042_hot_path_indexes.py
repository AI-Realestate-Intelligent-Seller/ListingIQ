"""Composite indexes for the dashboard's hottest reads.

- messages (conversation_id, direction): "which threads have a reply", per-thread
  inbound/outbound counts (Follow-ups, overviews).
- messages (conversation_id, created_at, id): latest message per thread
  (Follow-ups cards, SMS inbox) — matches their ORDER BY exactly.
- leads (user_id, campaign_id): the Lead Pool's "brokerage leads not in a
  campaign" filter.
- notifications (user_id, is_read): the bell's unread list and count.

Revision ID: 0042_hot_path_indexes
Revises: 0041_lofty_exports
"""

import sqlalchemy as sa
from alembic import op

revision = '0042_hot_path_indexes'
down_revision = '0041_lofty_exports'
branch_labels = None
depends_on = None

INDEXES = (
    ('ix_messages_conversation_direction', 'messages', ['conversation_id', 'direction']),
    ('ix_messages_conversation_created', 'messages', ['conversation_id', 'created_at', 'id']),
    ('ix_leads_user_campaign', 'leads', ['user_id', 'campaign_id']),
    ('ix_notifications_user_read', 'notifications', ['user_id', 'is_read']),
)


def _has_index(table: str, index: str) -> bool:
    return index in {row['name'] for row in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade():
    for name, table, columns in INDEXES:
        if not _has_index(table, name):
            op.create_index(name, table, columns)


def downgrade():
    for name, table, _columns in reversed(INDEXES):
        if _has_index(table, name):
            op.drop_index(name, table_name=table)
