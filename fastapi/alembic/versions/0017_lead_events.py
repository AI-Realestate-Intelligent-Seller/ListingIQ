"""Lead events — one history table for a lead's full lifecycle timeline.

Stage moves, assignment/revoke/reassign, AI<->agent ownership handoffs, and
notable activity (meeting booked, etc.) all land here as typed rows instead of
being scattered across the three status-ish fields the app already had
(Lead.assignment_stage, the derived pool stage, Conversation.lead_status).

Revision ID: 0017_lead_events
Revises: 0016_bobbie_followup_cadence
Create Date: 2026-08-31 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0017_lead_events'
down_revision = '0016_bobbie_followup_cadence'
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _inspector().get_table_names()


def _has_index(table: str, index: str) -> bool:
    return index in {row['name'] for row in _inspector().get_indexes(table)}


def upgrade():
    # The app calls Base.metadata.create_all() on startup, so a database that
    # has already run the new model code may carry this table before the
    # migration does — hence the guard, matching every other table added here.
    if not _has_table('lead_events'):
        op.create_table(
            'lead_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('lead_id', sa.Integer(), sa.ForeignKey('leads.id'), nullable=False),
            sa.Column('event_category', sa.String(20), nullable=False),
            sa.Column('event_type', sa.String(50), nullable=False),
            sa.Column('actor_type', sa.String(20), nullable=False, server_default='system'),
            sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('target_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
            sa.Column('from_value', sa.String(100), nullable=True),
            sa.Column('to_value', sa.String(100), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('meta', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
    if not _has_index('lead_events', 'ix_lead_events_lead_id'):
        op.create_index('ix_lead_events_lead_id', 'lead_events', ['lead_id'])
    if not _has_index('lead_events', 'ix_lead_events_created_at'):
        op.create_index('ix_lead_events_created_at', 'lead_events', ['created_at'])


def downgrade():
    if _has_index('lead_events', 'ix_lead_events_created_at'):
        op.drop_index('ix_lead_events_created_at', table_name='lead_events')
    if _has_index('lead_events', 'ix_lead_events_lead_id'):
        op.drop_index('ix_lead_events_lead_id', table_name='lead_events')
    if _has_table('lead_events'):
        op.drop_table('lead_events')
