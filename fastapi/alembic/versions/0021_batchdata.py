"""BatchData PAYG integration state, immutable properties, history and files."""

import sqlalchemy as sa
from alembic import op

revision = "0021_batchdata"
down_revision = "0020_propertyradar"
branch_labels = None
depends_on = None


def _create_table(name, *columns):
    inspector = sa.inspect(op.get_bind())
    if name in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns(name)}
        expected = {column.name for column in columns if isinstance(column, sa.Column)}
        if not expected.issubset(existing):
            raise RuntimeError(f"Existing {name} schema is incomplete; reconcile before migrating")
        return
    op.create_table(name, *columns)


def _create_index(name, table, columns):
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns)


def upgrade():
    _create_table(
        "batchdata_runs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("selected_categories", sa.JSON(), nullable=False),
        sa.Column("call_plan", sa.JSON(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("actual_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("returned_records", sa.Integer(), nullable=False),
        sa.Column("unique_properties", sa.Integer(), nullable=False),
        sa.Column("duplicate_properties", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    _create_index("ix_batchdata_runs_status", "batchdata_runs", ["status"])
    _create_table(
        "batchdata_properties",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_property_id", sa.String(160), nullable=False),
        sa.Column("address_hash", sa.String(160)),
        sa.Column("apn", sa.String(160)),
        sa.Column("normalized_address", sa.String(600)),
        sa.Column("immutable_provider_snapshot", sa.JSON(), nullable=False),
        sa.Column("operational_copy", sa.JSON(), nullable=False),
        sa.Column("skiptrace_status", sa.String(40), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "provider_property_id", name="uq_batchdata_provider_property"),
    )
    _create_table(
        "batchdata_property_memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("property_id", sa.Integer(), sa.ForeignKey("batchdata_properties.id"), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("first_matched_at", sa.DateTime(timezone=True)),
        sa.Column("last_matched_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("property_id", "category", name="uq_batchdata_property_category"),
    )
    _create_table(
        "batchdata_api_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("batchdata_runs.id")),
        sa.Column("endpoint", sa.String(300), nullable=False),
        sa.Column("product", sa.String(60), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON()),
        sa.Column("returned_records", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("actual_cost", sa.Numeric(12, 4), nullable=False),
        sa.Column("request_id", sa.String(160)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    _create_index("ix_batchdata_api_calls_status", "batchdata_api_calls", ["status"])
    _create_table(
        "batchdata_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(64), nullable=False, unique=True),
        sa.Column("provider_property_id", sa.String(160)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
    )
    _create_index("ix_batchdata_webhook_events_property", "batchdata_webhook_events", ["provider_property_id"])
    _create_index("ix_batchdata_webhook_events_status", "batchdata_webhook_events", ["status"])
    _create_table(
        "batchdata_saved_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("batchdata_runs.id")),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("relative_path", sa.String(1000), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
    )
    _create_table(
        "batchdata_reservations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.String(64), sa.ForeignKey("batchdata_runs.id"), nullable=False, unique=True),
        sa.Column("amount", sa.Numeric(12, 4), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
    )


def downgrade():
    op.drop_table("batchdata_reservations")
    op.drop_table("batchdata_saved_files")
    op.drop_index("ix_batchdata_webhook_events_status", table_name="batchdata_webhook_events")
    op.drop_index("ix_batchdata_webhook_events_property", table_name="batchdata_webhook_events")
    op.drop_table("batchdata_webhook_events")
    op.drop_index("ix_batchdata_api_calls_status", table_name="batchdata_api_calls")
    op.drop_table("batchdata_api_calls")
    op.drop_table("batchdata_property_memberships")
    op.drop_table("batchdata_properties")
    op.drop_index("ix_batchdata_runs_status", table_name="batchdata_runs")
    op.drop_table("batchdata_runs")
