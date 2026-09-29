"""Store the user's timezone for reminder messages."""

from alembic import op
import sqlalchemy as sa

revision = '0027_user_timezone'
down_revision = '0026_notification_reminder'
branch_labels = None
depends_on = None


def upgrade():
    columns = sa.inspect(op.get_bind()).get_columns('users')
    if not any(column['name'] == 'timezone' for column in columns):
        op.add_column('users', sa.Column('timezone', sa.String(100), nullable=True))


def downgrade():
    columns = sa.inspect(op.get_bind()).get_columns('users')
    if any(column['name'] == 'timezone' for column in columns):
        with op.batch_alter_table('users') as batch:
            batch.drop_column('timezone')
