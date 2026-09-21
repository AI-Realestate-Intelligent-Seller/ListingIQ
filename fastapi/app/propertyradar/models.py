"""Platform property inventory; tenant-owned Leads are outreach prospects, not properties."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.types import TypeDecorator

from ..db import Base


class UTCDateTime(TypeDecorator):
    """SQLite strips timezone metadata; restore UTC on every database read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("PropertyRadar timestamps must be timezone-aware")
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        return (
            value.replace(tzinfo=timezone.utc)
            if value is not None and value.tzinfo is None
            else value
        )


def utcnow():
    return datetime.now(timezone.utc)


class IntegrationConfig(Base):
    __tablename__ = "integration_configs"
    id = Column(Integer, primary_key=True)
    provider = Column(String(40), unique=True, nullable=False)
    enabled = Column(Boolean, default=False, nullable=False)
    connection_status = Column(String(30), default="not_tested", nullable=False)
    configuration_json = Column(JSON, default=dict, nullable=False)
    lock_token = Column(String(64))
    lock_started_at = Column(UTCDateTime())
    webhook_id = Column(String(80))
    last_connection_test_at = Column(UTCDateTime())
    last_successful_connection_at = Column(UTCDateTime())
    last_successful_sync_at = Column(UTCDateTime())
    last_error = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Category(Base):
    __tablename__ = "propertyradar_categories"
    id = Column(Integer, primary_key=True)
    key = Column(String(50), unique=True, nullable=False)
    label = Column(String(100), nullable=False)
    criteria_json = Column(JSON, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ProviderList(Base):
    __tablename__ = "propertyradar_lists"
    id = Column(Integer, primary_key=True)
    category_id = Column(
        Integer, ForeignKey("propertyradar_categories.id"), nullable=False
    )
    provider_list_id = Column(String(80), unique=True)
    list_name = Column(String(100), nullable=False)
    criteria_json = Column(JSON, nullable=False)
    total_count = Column(Integer)
    unique_count_at_validation = Column(Integer)
    is_monitored = Column(Boolean, default=False)
    monitoring_started_at = Column(UTCDateTime())
    last_synced_at = Column(UTCDateTime())
    status = Column(String(40), default="prepared")
    automation_status = Column(String(40), default="not_configured")
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Property(Base):
    __tablename__ = "provider_properties"
    id = Column(Integer, primary_key=True)
    radar_id = Column(String(100), unique=True, nullable=False)
    first_seen_at = Column(UTCDateTime(), default=utcnow)
    first_seen_source = Column(String(40), nullable=False)
    propertyradar_details_fetched_at = Column(UTCDateTime())
    propertyradar_skiptrace_status = Column(String(40), default="not_requested")
    current_provider_payload = Column(JSON, default=dict, nullable=False)


class Membership(Base):
    __tablename__ = "property_category_memberships"
    __table_args__ = (UniqueConstraint("property_id", "category_id"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("provider_properties.id"), nullable=False)
    category_id = Column(
        Integer, ForeignKey("propertyradar_categories.id"), nullable=False
    )
    provider_list_id = Column(String(80))
    first_matched_at = Column(UTCDateTime(), default=utcnow)
    last_matched_at = Column(UTCDateTime(), default=utcnow)
    match_source = Column(String(40))
    is_active = Column(Boolean, default=True)


class Snapshot(Base):
    __tablename__ = "property_provider_snapshots"
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("provider_properties.id"), nullable=False)
    provider = Column(String(40), default="propertyradar")
    snapshot_type = Column(String(40))
    trigger_type = Column(String(40))
    payload_hash = Column(String(64), nullable=False)
    raw_payload = Column(JSON, nullable=False)
    captured_at = Column(UTCDateTime(), default=utcnow)
    created_at = Column(UTCDateTime(), default=utcnow)


@event.listens_for(Snapshot, "before_update")
@event.listens_for(Snapshot, "before_delete")
def immutable_snapshot(*_):
    raise ValueError("Provider snapshots are immutable")


class WebhookEvent(Base):
    __tablename__ = "propertyradar_webhook_events"
    id = Column(Integer, primary_key=True)
    radar_id = Column(String(100), index=True)
    provider_list_id = Column(String(80))
    list_name = Column(String(200))
    trigger_type = Column(String(40))
    new_record_type = Column(String(100))
    change_1 = Column(Text)
    change_2 = Column(Text)
    change_3 = Column(Text)
    payload_hash = Column(String(64), index=True, nullable=False)
    raw_payload = Column(JSON, nullable=False)
    received_at = Column(UTCDateTime(), default=utcnow)
    processed_at = Column(UTCDateTime())
    processing_status = Column(String(40), default="pending", index=True)
    is_retry_duplicate = Column(Boolean, default=False)
    is_duplicate_property = Column(Boolean, default=False)
    is_test = Column(Boolean, default=False)
    error_message = Column(Text)


class SkiptraceJob(Base):
    __tablename__ = "propertyradar_skiptrace_jobs"
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("provider_properties.id"), unique=True, nullable=False
    )
    radar_id = Column(String(100), nullable=False)
    status = Column(String(40), default="pending", index=True)
    queued_at = Column(UTCDateTime(), default=utcnow)
    started_at = Column(UTCDateTime())
    completed_at = Column(UTCDateTime())
    deferred_until = Column(UTCDateTime())
    selected_contact_mode = Column(String(30), default="primary")
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Phone(Base):
    __tablename__ = "property_phones"
    __table_args__ = (UniqueConstraint("property_id", "normalized_phone"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("provider_properties.id"), nullable=False)
    person_key = Column(String(100))
    normalized_phone = Column(String(50), nullable=False)
    phone_type = Column(String(50))
    status = Column(String(50))
    source = Column(String(50), default="propertyradar")
    fetched_at = Column(UTCDateTime(), default=utcnow)


class Email(Base):
    __tablename__ = "property_emails"
    __table_args__ = (UniqueConstraint("property_id", "normalized_email"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("provider_properties.id"), nullable=False)
    person_key = Column(String(100))
    normalized_email = Column(String(320), nullable=False)
    status = Column(String(50))
    source = Column(String(50), default="propertyradar")
    fetched_at = Column(UTCDateTime(), default=utcnow)


class Usage(Base):
    __tablename__ = "propertyradar_usage"
    id = Column(Integer, primary_key=True)
    billing_cycle = Column(String(20), index=True)
    usage_type = Column(String(40), nullable=False)
    radar_id = Column(String(100))
    person_key = Column(String(100))
    endpoint = Column(String(300))
    preview = Column(Boolean, default=False)
    purchased = Column(Boolean, default=False)
    result_count = Column(Integer, default=0)
    quantity_free_remaining = Column(Integer)
    total_cost = Column(Numeric(12, 4))
    request_id = Column(String(100))
    operation_key = Column(String(200), unique=True)
    status = Column(String(40), default="ok")
    error_message = Column(Text)
    response_payload = Column(JSON)
    created_at = Column(UTCDateTime(), default=utcnow)


class Preview(Base):
    __tablename__ = "propertyradar_previews"
    id = Column(String(64), primary_key=True)
    kind = Column(String(40), nullable=False)
    config_hash = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False)
    consumed = Column(Boolean, default=False)
    created_at = Column(UTCDateTime(), default=utcnow)
