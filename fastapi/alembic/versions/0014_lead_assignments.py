"""Persist lead-to-agent assignments and link legacy agents when unambiguous

Revision ID: 0014_lead_assignments
Revises: 0013_agent_broker_assignment
Create Date: 2026-08-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0014_lead_assignments'
down_revision = '0013_agent_broker_assignment'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {item['name'] for item in inspector.get_columns('leads')}
    if 'assigned_agent_id' not in columns:
        op.add_column('leads', sa.Column('assigned_agent_id', sa.Integer(), nullable=True))
    indexes = {item['name'] for item in sa.inspect(op.get_bind()).get_indexes('leads')}
    if 'ix_leads_assigned_agent_id' not in indexes:
        op.create_index('ix_leads_assigned_agent_id', 'leads', ['assigned_agent_id'])

    # Before broker selection existed, accepted agents had no link. Only infer
    # one where the brokerage has exactly one active broker; ambiguity stays null.
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE users SET assigned_broker_id = ("
        " SELECT MIN(b.id) FROM users b"
        " WHERE b.brokerage_id = users.brokerage_id AND b.role = 'broker' AND b.is_active = TRUE"
        ") WHERE users.role = 'agent' AND users.assigned_broker_id IS NULL"
        " AND 1 = (SELECT COUNT(*) FROM users b WHERE b.brokerage_id = users.brokerage_id"
        " AND b.role = 'broker' AND b.is_active = TRUE)"
    ))


def downgrade():
    indexes = {item['name'] for item in sa.inspect(op.get_bind()).get_indexes('leads')}
    if 'ix_leads_assigned_agent_id' in indexes:
        op.drop_index('ix_leads_assigned_agent_id', table_name='leads')
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('leads')}
    if 'assigned_agent_id' in columns:
        op.drop_column('leads', 'assigned_agent_id')
