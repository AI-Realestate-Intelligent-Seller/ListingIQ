"""Track CVAT-style assignment stages and time

Revision ID: 0015_assignment_workflow
Revises: 0014_lead_assignments
Create Date: 2026-08-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = '0015_assignment_workflow'
down_revision = '0014_lead_assignments'
branch_labels = None
depends_on = None


def upgrade():
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('leads')}
    if 'assignment_stage' not in columns:
        op.add_column('leads', sa.Column('assignment_stage', sa.String(20), nullable=False, server_default='new'))
    if 'assignment_stage_changed_at' not in columns:
        op.add_column('leads', sa.Column('assignment_stage_changed_at', sa.DateTime(), nullable=True))
    if 'assignment_stage_seconds' not in columns:
        op.add_column('leads', sa.Column('assignment_stage_seconds', sa.Text(), nullable=False, server_default='{}'))
    op.get_bind().execute(sa.text(
        "UPDATE leads SET assignment_stage_changed_at = CURRENT_TIMESTAMP "
        "WHERE assigned_agent_id IS NOT NULL AND assignment_stage_changed_at IS NULL"
    ))


def downgrade():
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('leads')}
    for column in ('assignment_stage_seconds', 'assignment_stage_changed_at', 'assignment_stage'):
        if column in columns:
            op.drop_column('leads', column)
