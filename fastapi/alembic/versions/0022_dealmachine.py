"""DealMachine integration, immutable history, contacts, lists and exports."""

import sqlalchemy as sa
from alembic import op

revision = "0022_dealmachine"
down_revision = "0021_batchdata"
branch_labels = None
depends_on = None

TABLES = [
    "dealmachine_integrations", "dealmachine_config_versions", "dealmachine_metadata_cache",
    "dealmachine_categories", "dealmachine_runs", "dealmachine_executions",
    "dealmachine_properties", "dealmachine_snapshots", "dealmachine_property_categories",
    "dealmachine_property_changes", "dealmachine_enrichment_queue", "dealmachine_people",
    "dealmachine_property_contacts", "dealmachine_contact_points", "dealmachine_credit_usage",
    "dealmachine_lists", "dealmachine_list_membership_events", "dealmachine_saved_files",
    "dealmachine_exports",
]


def upgrade():
    # Models are the single schema source in this young codebase; startup also
    # uses create_all. Verify any pre-existing table before accepting it.
    from app.db import Base
    from app import models as core_models  # noqa: F401
    from app.dealmachine import models  # noqa: F401

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    for name in TABLES:
        table = Base.metadata.tables[name]
        if name in existing:
            actual = {item["name"] for item in inspector.get_columns(name)}
            expected = {column.name for column in table.columns}
            if not expected.issubset(actual):
                raise RuntimeError(f"Existing {name} schema is incomplete; reconcile before migrating")
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[name] for name in TABLES], checkfirst=True)


def downgrade():
    for name in reversed(TABLES):
        if name in sa.inspect(op.get_bind()).get_table_names():
            op.drop_table(name)
