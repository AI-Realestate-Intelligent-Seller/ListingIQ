"""Add the durable outbound SMS submission queue."""

from alembic import op
import sqlalchemy as sa


revision = '0030_outbound_sms_jobs'
down_revision = '0029_message_failure_details'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'outbound_sms_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_key', sa.String(length=160), nullable=False),
        sa.Column('campaign_id', sa.Integer(), nullable=False),
        sa.Column('lead_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.Integer(), nullable=True),
        sa.Column('to_number', sa.String(length=50), nullable=False),
        sa.Column('property_key', sa.String(length=500), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('available_at', sa.DateTime(), nullable=False),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.Column('worker_id', sa.String(length=80), nullable=True),
        sa.Column('telnyx_id', sa.String(length=128), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['message_id'], ['messages.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_key'),
        sa.UniqueConstraint('message_id'),
    )
    op.create_index('ix_outbound_sms_jobs_campaign_id', 'outbound_sms_jobs', ['campaign_id'])
    op.create_index('ix_outbound_sms_jobs_job_key', 'outbound_sms_jobs', ['job_key'])
    op.create_index('ix_outbound_sms_jobs_lead_id', 'outbound_sms_jobs', ['lead_id'])
    op.create_index('ix_outbound_sms_jobs_ready', 'outbound_sms_jobs', ['status', 'available_at'])
    op.create_index('ix_outbound_sms_jobs_status', 'outbound_sms_jobs', ['status'])
    op.create_index('ix_outbound_sms_jobs_telnyx_id', 'outbound_sms_jobs', ['telnyx_id'])
    op.create_index('ix_outbound_sms_jobs_user_id', 'outbound_sms_jobs', ['user_id'])


def downgrade():
    op.drop_table('outbound_sms_jobs')
