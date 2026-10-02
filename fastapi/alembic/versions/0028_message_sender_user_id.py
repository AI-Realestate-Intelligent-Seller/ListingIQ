"""Add human message attribution and conversation notification fields."""

from alembic import op
import sqlalchemy as sa

revision = '0028_message_sender_user_id'
down_revision = '0026_batchdata_storage_backend'
branch_labels = None
depends_on = None

INDEX_NAME = 'ix_messages_sender_user_id'
FK_NAME = 'fk_messages_sender_user_id_users'
NOTIFICATION_FK_NAME = 'fk_notifications_conversation_id_conversations'

NAMING_CONVENTION = {
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'
}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---------------------------------------------------------
    # messages.sender_user_id
    # ---------------------------------------------------------
    has_column = any(
        c['name'] == 'sender_user_id'
        for c in inspector.get_columns('messages')
    )

    has_foreign_key = any(
        fk['constrained_columns'] == ['sender_user_id']
        and fk['referred_table'] == 'users'
        and fk['referred_columns'] == ['id']
        for fk in inspector.get_foreign_keys('messages')
    )

    if not has_column or not has_foreign_key:
        with op.batch_alter_table(
            'messages',
            naming_convention=NAMING_CONVENTION,
        ) as batch:
            if not has_column:
                batch.add_column(
                    sa.Column(
                        'sender_user_id',
                        sa.Integer(),
                        nullable=True,
                    )
                )

            if not has_foreign_key:
                batch.create_foreign_key(
                    FK_NAME,
                    'users',
                    ['sender_user_id'],
                    ['id'],
                )

    inspector = sa.inspect(bind)

    if not any(
        i['name'] == INDEX_NAME
        for i in inspector.get_indexes('messages')
    ):
        op.create_index(
            INDEX_NAME,
            'messages',
            ['sender_user_id'],
        )

    # ---------------------------------------------------------
    # notifications.conversation_id + unread_count
    # ---------------------------------------------------------
    inspector = sa.inspect(bind)

    notification_columns = {
        c['name']
        for c in inspector.get_columns('notifications')
    }

    notification_foreign_keys = inspector.get_foreign_keys(
        'notifications'
    )

    has_conversation_fk = any(
        fk['constrained_columns'] == ['conversation_id']
        and fk['referred_table'] == 'conversations'
        and fk['referred_columns'] == ['id']
        for fk in notification_foreign_keys
    )

    with op.batch_alter_table(
        'notifications',
        naming_convention=NAMING_CONVENTION,
    ) as batch:

        if 'conversation_id' not in notification_columns:
            batch.add_column(
                sa.Column(
                    'conversation_id',
                    sa.Integer(),
                    nullable=True,
                )
            )

        if 'unread_count' not in notification_columns:
            batch.add_column(
                sa.Column(
                    'unread_count',
                    sa.Integer(),
                    nullable=False,
                    server_default='1',
                )
            )

        if not has_conversation_fk:
            batch.create_foreign_key(
                NOTIFICATION_FK_NAME,
                'conversations',
                ['conversation_id'],
                ['id'],
            )


def downgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # ---------------------------------------------------------
    # notifications
    # ---------------------------------------------------------
    notification_columns = {
        c['name']
        for c in inspector.get_columns('notifications')
    }

    notification_foreign_keys = inspector.get_foreign_keys(
        'notifications'
    )

    with op.batch_alter_table(
        'notifications',
        naming_convention=NAMING_CONVENTION,
    ) as batch:

        for fk in notification_foreign_keys:
            if fk['constrained_columns'] == ['conversation_id']:
                name = fk['name'] or NOTIFICATION_FK_NAME
                batch.drop_constraint(
                    name,
                    type_='foreignkey',
                )

        if 'unread_count' in notification_columns:
            batch.drop_column('unread_count')

        if 'conversation_id' in notification_columns:
            batch.drop_column('conversation_id')

    # ---------------------------------------------------------
    # messages.sender_user_id
    # ---------------------------------------------------------
    inspector = sa.inspect(bind)

    if not any(
        c['name'] == 'sender_user_id'
        for c in inspector.get_columns('messages')
    ):
        return

    foreign_keys = inspector.get_foreign_keys('messages')

    with op.batch_alter_table(
        'messages',
        naming_convention=NAMING_CONVENTION,
    ) as batch:

        if any(
            i['name'] == INDEX_NAME
            for i in inspector.get_indexes('messages')
        ):
            batch.drop_index(INDEX_NAME)

        for fk in foreign_keys:
            if fk['constrained_columns'] == ['sender_user_id']:
                name = (
                    fk['name']
                    or f"fk_messages_sender_user_id_{fk['referred_table']}"
                )
                batch.drop_constraint(
                    name,
                    type_='foreignkey',
                )

        batch.drop_column('sender_user_id')