"""Create push subscriptions, booking reminders, and optional-reminder notifications."""

from alembic import op
import sqlalchemy as sa


revision = '0026_notification_reminder'
down_revision = '0025_booking_location'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table('push_subscriptions'):
        op.create_table(
            'push_subscriptions',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
            sa.Column('device_id', sa.String(100), nullable=False),
            sa.Column('endpoint', sa.Text(), nullable=False, unique=True),
            sa.Column('p256dh', sa.Text(), nullable=False),
            sa.Column('auth', sa.Text(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
        )
        op.create_index('ix_push_subscriptions_user_id', 'push_subscriptions', ['user_id'])
        op.create_index('ix_push_subscriptions_device_id', 'push_subscriptions', ['device_id'])

    if not inspector.has_table('booking_reminders'):
        op.create_table(
            'booking_reminders',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('booking_id', sa.Integer(), sa.ForeignKey('bookings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('reminder_type', sa.String(50), nullable=False),
            sa.Column('scheduled_for', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('sent_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
        )
        op.create_index('ix_booking_reminders_booking_id', 'booking_reminders', ['booking_id'])
        op.create_index('ix_booking_reminders_user_id', 'booking_reminders', ['user_id'])
        op.create_index('ix_booking_reminders_status_scheduled_for', 'booking_reminders', ['status', 'scheduled_for'])

    if not inspector.has_table('notifications'):
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
            sa.Column('booking_id', sa.Integer(), sa.ForeignKey('bookings.id', ondelete='CASCADE'), nullable=True),
            sa.Column('reminder_id', sa.Integer(), sa.ForeignKey('booking_reminders.id', ondelete='SET NULL'), nullable=True, unique=True),
            sa.Column('type', sa.String(50), nullable=False),
            sa.Column('title', sa.String(255), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('is_read', sa.Boolean(), nullable=False),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('action_url', sa.String(500), nullable=True),
            sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversations.id'), nullable=True),
            sa.Column('unread_count', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
        )
        for name in ('user_id', 'booking_id', 'is_read', 'created_at'):
            op.create_index(f'ix_notifications_{name}', 'notifications', [name])
        return
    columns = {c['name']: c for c in inspector.get_columns('notifications')}
    with op.batch_alter_table('notifications') as batch:
        if 'conversation_id' not in columns:
            batch.add_column(sa.Column('conversation_id', sa.Integer(), nullable=True))
            batch.create_foreign_key('fk_notifications_conversation_id_conversations', 'conversations', ['conversation_id'], ['id'])
        if 'unread_count' not in columns:
            batch.add_column(sa.Column('unread_count', sa.Integer(), nullable=False, server_default='1'))
    column = columns['reminder_id']
    if not column['nullable']:
        with op.batch_alter_table('notifications') as batch:
            batch.alter_column('reminder_id', existing_type=sa.Integer(), nullable=True)


def downgrade():
    inspector = sa.inspect(op.get_bind())
    # Remove dependents first. Rolling back table creation removes their data.
    for name in ('notifications', 'booking_reminders', 'push_subscriptions'):
        if inspector.has_table(name):
            op.drop_table(name)
