"""PropertyRadar platform integration and durable inventory. Frozen schema."""

import sqlalchemy as sa

from alembic import op

revision = "0020_propertyradar"
down_revision = "0019_platform_admin"
branch_labels = None
depends_on = None


def _create_table(name, *columns):
    # Local FastAPI startup uses create_all; accommodate tables it already created.
    inspector = sa.inspect(op.get_bind())
    if name in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns(name)}
        expected = {column.name for column in columns if isinstance(column, sa.Column)}
        if not expected.issubset(existing):
            raise RuntimeError(
                f"Existing {name} schema is incomplete; reconcile before migrating"
            )
        return
    op.create_table(name, *columns)


def _create_index(name, table, columns, unique=False):
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, columns, unique=unique)


def upgrade():
    _create_table(
        "integration_configs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column(
            "connection_status", sa.String(length=30), primary_key=False, nullable=False
        ),
        sa.Column("configuration_json", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("lock_token", sa.String(length=64), primary_key=False, nullable=True),
        sa.Column(
            "lock_started_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("webhook_id", sa.String(length=80), primary_key=False, nullable=True),
        sa.Column(
            "last_connection_test_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "last_successful_connection_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "last_successful_sync_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("last_error", sa.Text(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("provider"),
    )
    _create_table(
        "propertyradar_categories",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("key", sa.String(length=50), primary_key=False, nullable=False),
        sa.Column("label", sa.String(length=100), primary_key=False, nullable=False),
        sa.Column("criteria_json", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("enabled", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("key"),
    )
    _create_table(
        "propertyradar_previews",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("kind", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column(
            "config_hash", sa.String(length=64), primary_key=False, nullable=False
        ),
        sa.Column("payload", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("consumed", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
    )
    _create_table(
        "propertyradar_usage",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "billing_cycle", sa.String(length=20), primary_key=False, nullable=True
        ),
        sa.Column(
            "usage_type", sa.String(length=40), primary_key=False, nullable=False
        ),
        sa.Column("radar_id", sa.String(length=100), primary_key=False, nullable=True),
        sa.Column(
            "person_key", sa.String(length=100), primary_key=False, nullable=True
        ),
        sa.Column("endpoint", sa.String(length=300), primary_key=False, nullable=True),
        sa.Column("preview", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column("purchased", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column("result_count", sa.Integer(), primary_key=False, nullable=True),
        sa.Column(
            "quantity_free_remaining", sa.Integer(), primary_key=False, nullable=True
        ),
        sa.Column(
            "total_cost",
            sa.Numeric(precision=12, scale=4),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "request_id", sa.String(length=100), primary_key=False, nullable=True
        ),
        sa.Column(
            "operation_key", sa.String(length=200), primary_key=False, nullable=True
        ),
        sa.Column("status", sa.String(length=40), primary_key=False, nullable=True),
        sa.Column("error_message", sa.Text(), primary_key=False, nullable=True),
        sa.Column("response_payload", sa.JSON(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("operation_key"),
    )
    _create_index(
        "ix_propertyradar_usage_billing_cycle",
        "propertyradar_usage",
        ["billing_cycle"],
        unique=False,
    )
    _create_table(
        "propertyradar_webhook_events",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("radar_id", sa.String(length=100), primary_key=False, nullable=True),
        sa.Column(
            "provider_list_id", sa.String(length=80), primary_key=False, nullable=True
        ),
        sa.Column("list_name", sa.String(length=200), primary_key=False, nullable=True),
        sa.Column(
            "trigger_type", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column(
            "new_record_type", sa.String(length=100), primary_key=False, nullable=True
        ),
        sa.Column("change_1", sa.Text(), primary_key=False, nullable=True),
        sa.Column("change_2", sa.Text(), primary_key=False, nullable=True),
        sa.Column("change_3", sa.Text(), primary_key=False, nullable=True),
        sa.Column(
            "payload_hash", sa.String(length=64), primary_key=False, nullable=False
        ),
        sa.Column("raw_payload", sa.JSON(), primary_key=False, nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "processing_status", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column("is_retry_duplicate", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column(
            "is_duplicate_property", sa.Boolean(), primary_key=False, nullable=True
        ),
        sa.Column("is_test", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column("error_message", sa.Text(), primary_key=False, nullable=True),
    )
    _create_index(
        "ix_propertyradar_webhook_events_processing_status",
        "propertyradar_webhook_events",
        ["processing_status"],
        unique=False,
    )
    _create_index(
        "ix_propertyradar_webhook_events_radar_id",
        "propertyradar_webhook_events",
        ["radar_id"],
        unique=False,
    )
    _create_index(
        "ix_propertyradar_webhook_events_payload_hash",
        "propertyradar_webhook_events",
        ["payload_hash"],
        unique=False,
    )
    _create_table(
        "provider_properties",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("radar_id", sa.String(length=100), primary_key=False, nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "first_seen_source", sa.String(length=40), primary_key=False, nullable=False
        ),
        sa.Column(
            "propertyradar_details_fetched_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "propertyradar_skiptrace_status",
            sa.String(length=40),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "current_provider_payload", sa.JSON(), primary_key=False, nullable=False
        ),
        sa.UniqueConstraint("radar_id"),
    )
    _create_table(
        "property_category_memberships",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("provider_properties.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("propertyradar_categories.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "provider_list_id", sa.String(length=80), primary_key=False, nullable=True
        ),
        sa.Column(
            "first_matched_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "last_matched_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "match_source", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column("is_active", sa.Boolean(), primary_key=False, nullable=True),
        sa.UniqueConstraint("property_id", "category_id"),
    )
    _create_table(
        "property_emails",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("provider_properties.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "person_key", sa.String(length=100), primary_key=False, nullable=True
        ),
        sa.Column(
            "normalized_email", sa.String(length=320), primary_key=False, nullable=False
        ),
        sa.Column("status", sa.String(length=50), primary_key=False, nullable=True),
        sa.Column("source", sa.String(length=50), primary_key=False, nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("property_id", "normalized_email"),
    )
    _create_table(
        "property_phones",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("provider_properties.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "person_key", sa.String(length=100), primary_key=False, nullable=True
        ),
        sa.Column(
            "normalized_phone", sa.String(length=50), primary_key=False, nullable=False
        ),
        sa.Column("phone_type", sa.String(length=50), primary_key=False, nullable=True),
        sa.Column("status", sa.String(length=50), primary_key=False, nullable=True),
        sa.Column("source", sa.String(length=50), primary_key=False, nullable=True),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("property_id", "normalized_phone"),
    )
    _create_table(
        "property_provider_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("provider_properties.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=40), primary_key=False, nullable=True),
        sa.Column(
            "snapshot_type", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column(
            "trigger_type", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column(
            "payload_hash", sa.String(length=64), primary_key=False, nullable=False
        ),
        sa.Column("raw_payload", sa.JSON(), primary_key=False, nullable=False),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
    )
    _create_table(
        "propertyradar_lists",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("propertyradar_categories.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column(
            "provider_list_id", sa.String(length=80), primary_key=False, nullable=True
        ),
        sa.Column(
            "list_name", sa.String(length=100), primary_key=False, nullable=False
        ),
        sa.Column("criteria_json", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("total_count", sa.Integer(), primary_key=False, nullable=True),
        sa.Column(
            "unique_count_at_validation", sa.Integer(), primary_key=False, nullable=True
        ),
        sa.Column("is_monitored", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column(
            "monitoring_started_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "last_synced_at",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("status", sa.String(length=40), primary_key=False, nullable=True),
        sa.Column(
            "automation_status", sa.String(length=40), primary_key=False, nullable=True
        ),
        sa.Column("error_message", sa.Text(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("provider_list_id"),
    )
    _create_table(
        "propertyradar_skiptrace_jobs",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column(
            "property_id",
            sa.Integer(),
            sa.ForeignKey("provider_properties.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("radar_id", sa.String(length=100), primary_key=False, nullable=False),
        sa.Column("status", sa.String(length=40), primary_key=False, nullable=True),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "deferred_until",
            sa.DateTime(timezone=True),
            primary_key=False,
            nullable=True,
        ),
        sa.Column(
            "selected_contact_mode",
            sa.String(length=30),
            primary_key=False,
            nullable=True,
        ),
        sa.Column("error_message", sa.Text(), primary_key=False, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), primary_key=False, nullable=True
        ),
        sa.UniqueConstraint("property_id"),
    )
    _create_index(
        "ix_propertyradar_skiptrace_jobs_status",
        "propertyradar_skiptrace_jobs",
        ["status"],
        unique=False,
    )


def downgrade():
    op.drop_table("propertyradar_skiptrace_jobs")
    op.drop_table("propertyradar_lists")
    op.drop_table("property_provider_snapshots")
    op.drop_table("property_phones")
    op.drop_table("property_emails")
    op.drop_table("property_category_memberships")
    op.drop_table("provider_properties")
    op.drop_table("propertyradar_webhook_events")
    op.drop_table("propertyradar_usage")
    op.drop_table("propertyradar_previews")
    op.drop_table("propertyradar_categories")
    op.drop_table("integration_configs")
