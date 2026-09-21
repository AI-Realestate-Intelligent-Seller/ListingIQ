"""Combined provider inventory and brokerage distribution history."""

from sqlalchemy import inspect

from alembic import op

revision = "0023_integration_distribution"
down_revision = "0022_dealmachine"
branch_labels = None
depends_on = None
TABLES = ["integration_distribution_runs", "integration_combined_properties"]


def upgrade():
    from app import models as core_models  # noqa: F401
    from app.db import Base
    from app.integration_data import models  # noqa: F401

    bind = op.get_bind()
    for name in TABLES:
        if name in inspect(bind).get_table_names():
            actual = {column["name"] for column in inspect(bind).get_columns(name)}
            expected = {column.name for column in Base.metadata.tables[name].columns}
            if not expected.issubset(actual):
                raise RuntimeError(f"Existing {name} schema is incomplete")
    Base.metadata.create_all(
        bind, tables=[Base.metadata.tables[name] for name in TABLES]
    )


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
