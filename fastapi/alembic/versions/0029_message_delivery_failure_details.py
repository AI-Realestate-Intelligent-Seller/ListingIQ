"""Store carrier failure details for outbound messages."""

from alembic import op
import sqlalchemy as sa


revision = '0029_message_failure_details'
down_revision = '0028_message_sender_user_id'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('messages')}
    with op.batch_alter_table('messages') as batch:
        if 'failure_code' not in columns:
            batch.add_column(sa.Column('failure_code', sa.String(length=50), nullable=True))
        if 'failure_reason' not in columns:
            batch.add_column(sa.Column('failure_reason', sa.Text(), nullable=True))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('messages')}
    with op.batch_alter_table('messages') as batch:
        if 'failure_reason' in columns:
            batch.drop_column('failure_reason')
        if 'failure_code' in columns:
            batch.drop_column('failure_code')
