"""Attribute human messages to the user who sent them."""

from alembic import op
import sqlalchemy as sa

revision = '0028_message_sender_user_id'
down_revision = '0027_user_timezone'
branch_labels = None
depends_on = None

INDEX_NAME = 'ix_messages_sender_user_id'
FK_NAME = 'fk_messages_sender_user_id_users'
NAMING_CONVENTION = {'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s'}


def upgrade():
    inspector = sa.inspect(op.get_bind())
    has_column = any(c['name'] == 'sender_user_id' for c in inspector.get_columns('messages'))
    has_foreign_key = any(
        fk['constrained_columns'] == ['sender_user_id']
        and fk['referred_table'] == 'users'
        and fk['referred_columns'] == ['id']
        for fk in inspector.get_foreign_keys('messages')
    )
    if not has_column or not has_foreign_key:
        # Batch mode supports SQLite's table rebuild and PostgreSQL ALTER TABLE.
        with op.batch_alter_table('messages', naming_convention=NAMING_CONVENTION) as batch:
            if not has_column:
                # Historical, customer, and AI messages retain a NULL sender.
                batch.add_column(sa.Column('sender_user_id', sa.Integer(), nullable=True))
            if not has_foreign_key:
                batch.create_foreign_key(FK_NAME, 'users', ['sender_user_id'], ['id'])

    if not any(i['name'] == INDEX_NAME for i in sa.inspect(op.get_bind()).get_indexes('messages')):
        op.create_index(INDEX_NAME, 'messages', ['sender_user_id'])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if not any(c['name'] == 'sender_user_id' for c in inspector.get_columns('messages')):
        return
    foreign_keys = inspector.get_foreign_keys('messages')
    with op.batch_alter_table('messages', naming_convention=NAMING_CONVENTION) as batch:
        if any(i['name'] == INDEX_NAME for i in inspector.get_indexes('messages')):
            batch.drop_index(INDEX_NAME)
        for fk in foreign_keys:
            if fk['constrained_columns'] == ['sender_user_id']:
                name = fk['name'] or f"fk_messages_sender_user_id_{fk['referred_table']}"
                batch.drop_constraint(name, type_='foreignkey')
        batch.drop_column('sender_user_id')
