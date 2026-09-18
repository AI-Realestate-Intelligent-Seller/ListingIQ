from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)

from ..db import Base
from ..propertyradar.models import UTCDateTime, utcnow


class Integration(Base):
    __tablename__ = "dealmachine_integrations"
    id = Column(Integer, primary_key=True)
    provider = Column(String(40), unique=True, nullable=False, default="dealmachine")
    enabled = Column(Boolean, nullable=False, default=False)
    encrypted_api_key = Column(Text)
    key_hint = Column(String(20))
    connection_status = Column(String(30), nullable=False, default="disconnected")
    account_json = Column(JSON, nullable=False, default=dict)
    usage_json = Column(JSON, nullable=False, default=dict)
    settings_json = Column(JSON, nullable=False, default=dict)
    last_tested_at = Column(UTCDateTime())
    last_successful_sync_at = Column(UTCDateTime())
    next_scheduled_sync_at = Column(UTCDateTime())
    last_error = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ConfigVersion(Base):
    __tablename__ = "dealmachine_config_versions"
    id = Column(Integer, primary_key=True)
    version = Column(Integer, nullable=False, unique=True)
    configuration_json = Column(JSON, nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime(), default=utcnow)


class MetadataCache(Base):
    __tablename__ = "dealmachine_metadata_cache"
    __table_args__ = (UniqueConstraint("kind", "external_id"),)
    id = Column(Integer, primary_key=True)
    kind = Column(String(20), nullable=False, index=True)
    external_id = Column(String(160), nullable=False)
    name = Column(String(300))
    group_name = Column(String(160))
    metadata_json = Column(JSON, nullable=False)
    fetched_at = Column(UTCDateTime(), default=utcnow)


class Category(Base):
    __tablename__ = "dealmachine_categories"
    id = Column(Integer, primary_key=True)
    key = Column(String(60), unique=True, nullable=False)
    label = Column(String(120), nullable=False)
    enabled = Column(Boolean, nullable=False, default=False)
    support_status = Column(String(30), nullable=False, default="unconfirmed")
    configuration_json = Column(JSON, nullable=False, default=dict)
    last_run_at = Column(UTCDateTime())
    next_run_at = Column(UTCDateTime())
    last_result_count = Column(Integer, nullable=False, default=0)
    new_match_count = Column(Integer, nullable=False, default=0)
    changed_record_count = Column(Integer, nullable=False, default=0)
    credits_used = Column(Integer, nullable=False, default=0)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Run(Base):
    __tablename__ = "dealmachine_runs"
    id = Column(String(64), primary_key=True)
    trigger_type = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"))
    idempotency_key = Column(String(160), unique=True)
    categories_json = Column(JSON, nullable=False, default=list)
    configuration_snapshot = Column(JSON, nullable=False)
    summary_json = Column(JSON, nullable=False, default=dict)
    error_message = Column(Text)
    started_at = Column(UTCDateTime(), default=utcnow)
    completed_at = Column(UTCDateTime())


class Execution(Base):
    __tablename__ = "dealmachine_executions"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("dealmachine_runs.id"), index=True)
    sequence = Column(Integer, nullable=False, default=1)
    endpoint = Column(String(240), nullable=False, index=True)
    method = Column(String(10), nullable=False)
    category = Column(String(60), index=True)
    actor_id = Column(Integer, ForeignKey("users.id"))
    request_json = Column(JSON, nullable=False, default=dict)
    response_json = Column(JSON)
    response_headers_json = Column(JSON, nullable=False, default=dict)
    request_id = Column(String(160), index=True)
    external_activity_id = Column(String(160), unique=True)
    payload_hash = Column(String(64))
    credits_json = Column(JSON, nullable=False, default=dict)
    requested_rows = Column(Integer, nullable=False, default=0)
    returned_rows = Column(Integer, nullable=False, default=0)
    status = Column(String(30), nullable=False, index=True)
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    completed_at = Column(UTCDateTime())


@event.listens_for(Execution, "before_update")
def immutable_execution_payload(_, __, target):
    state = inspect(target)
    protected = (
        "endpoint",
        "method",
        "request_json",
        "response_json",
        "response_headers_json",
        "request_id",
        "payload_hash",
        "credits_json",
        "created_at",
    )
    status_history = state.attrs.status.history
    prior_status = (
        status_history.deleted[0] if status_history.deleted else target.status
    )
    if prior_status in {"succeeded", "failed", "partial"} and any(
        state.attrs[name].history.has_changes() for name in protected
    ):
        raise ValueError("Original DealMachine execution payloads are immutable")


@event.listens_for(Execution, "before_delete")
def append_only_execution(*_):
    raise ValueError("DealMachine execution history is append-only")


class Property(Base):
    __tablename__ = "dealmachine_properties"
    __table_args__ = (UniqueConstraint("provider", "provider_property_id"),)
    id = Column(Integer, primary_key=True)
    provider = Column(String(40), nullable=False, default="dealmachine")
    provider_property_id = Column(String(160), nullable=False, index=True)
    operational_json = Column(JSON, nullable=False, default=dict)
    first_seen_at = Column(UTCDateTime(), default=utcnow)
    last_seen_at = Column(UTCDateTime(), default=utcnow)
    first_run_id = Column(String(64), ForeignKey("dealmachine_runs.id"))
    last_run_id = Column(String(64), ForeignKey("dealmachine_runs.id"))
    last_payload_hash = Column(String(64))
    change_detected_at = Column(UTCDateTime())
    enrichment_queued_at = Column(UTCDateTime())
    enriched_at = Column(UTCDateTime())
    enrichment_state = Column(String(30), nullable=False, default="not_queued")
    review_status = Column(String(30), nullable=False, default="new")
    lead_pool_status = Column(String(30), nullable=False, default="not_added")
    internal_owner_id = Column(Integer, ForeignKey("users.id"))
    source_created_at = Column(UTCDateTime())
    source_updated_at = Column(UTCDateTime())


class Snapshot(Base):
    __tablename__ = "dealmachine_snapshots"
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("dealmachine_properties.id"), nullable=False, index=True
    )
    execution_id = Column(
        Integer, ForeignKey("dealmachine_executions.id"), nullable=False
    )
    snapshot_type = Column(String(30), nullable=False)
    payload_hash = Column(String(64), nullable=False, index=True)
    raw_payload = Column(JSON, nullable=False)
    created_at = Column(UTCDateTime(), default=utcnow)


@event.listens_for(Snapshot, "before_update")
@event.listens_for(Snapshot, "before_delete")
def immutable_snapshot(*_):
    raise ValueError("DealMachine snapshots are immutable")


class Membership(Base):
    __tablename__ = "dealmachine_property_categories"
    __table_args__ = (UniqueConstraint("property_id", "category_id"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("dealmachine_properties.id"), nullable=False
    )
    category_id = Column(
        Integer, ForeignKey("dealmachine_categories.id"), nullable=False
    )
    active = Column(Boolean, nullable=False, default=True)
    first_matched_at = Column(UTCDateTime(), default=utcnow)
    last_matched_at = Column(UTCDateTime(), default=utcnow)
    no_longer_matched_at = Column(UTCDateTime())


class ChangeEvent(Base):
    __tablename__ = "dealmachine_property_changes"
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("dealmachine_properties.id"), nullable=False
    )
    run_id = Column(String(64), ForeignKey("dealmachine_runs.id"), nullable=False)
    before_hash = Column(String(64))
    after_hash = Column(String(64), nullable=False)
    changed_fields_json = Column(JSON, nullable=False, default=list)
    created_at = Column(UTCDateTime(), default=utcnow)


class EnrichmentJob(Base):
    __tablename__ = "dealmachine_enrichment_queue"
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("dealmachine_properties.id"), nullable=False, index=True
    )
    status = Column(String(30), nullable=False, default="pending", index=True)
    forced = Column(Boolean, nullable=False, default=False)
    reason = Column(Text)
    actor_id = Column(Integer, ForeignKey("users.id"))
    queued_at = Column(UTCDateTime(), default=utcnow)
    started_at = Column(UTCDateTime())
    completed_at = Column(UTCDateTime())
    error_message = Column(Text)


class Person(Base):
    __tablename__ = "dealmachine_people"
    __table_args__ = (UniqueConstraint("provider", "provider_person_id"),)
    id = Column(Integer, primary_key=True)
    provider = Column(String(40), nullable=False, default="dealmachine")
    provider_person_id = Column(String(160), nullable=False)
    current_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PropertyContact(Base):
    __tablename__ = "dealmachine_property_contacts"
    __table_args__ = (UniqueConstraint("property_id", "person_id"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(
        Integer, ForeignKey("dealmachine_properties.id"), nullable=False
    )
    person_id = Column(Integer, ForeignKey("dealmachine_people.id"), nullable=False)


class ContactPoint(Base):
    __tablename__ = "dealmachine_contact_points"
    __table_args__ = (UniqueConstraint("person_id", "kind", "normalized_value"),)
    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("dealmachine_people.id"), nullable=False)
    kind = Column(String(10), nullable=False)
    normalized_value = Column(String(500), nullable=False)
    display_value = Column(String(500), nullable=False)
    contact_type = Column(String(60))
    do_not_call = Column(Boolean)
    metadata_json = Column(JSON, nullable=False, default=dict)


class CreditUsage(Base):
    __tablename__ = "dealmachine_credit_usage"
    id = Column(Integer, primary_key=True)
    execution_id = Column(
        Integer, ForeignKey("dealmachine_executions.id"), nullable=False, unique=True
    )
    cycle_start = Column(UTCDateTime())
    cycle_end = Column(UTCDateTime())
    used = Column(Integer, nullable=False, default=0)
    properties = Column(Integer, nullable=False, default=0)
    people = Column(Integer, nullable=False, default=0)
    provider_deduplicated = Column(Integer, nullable=False, default=0)
    local_duplicates = Column(Integer, nullable=False, default=0)
    created_at = Column(UTCDateTime(), default=utcnow)


class ProviderList(Base):
    __tablename__ = "dealmachine_lists"
    id = Column(Integer, primary_key=True)
    provider_list_id = Column(String(160), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    status = Column(String(30), nullable=False)
    total_count = Column(Integer, nullable=False, default=0)
    categories_json = Column(JSON, nullable=False, default=list)
    completed_at = Column(UTCDateTime())
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ListMembershipEvent(Base):
    __tablename__ = "dealmachine_list_membership_events"
    id = Column(Integer, primary_key=True)
    list_id = Column(Integer, ForeignKey("dealmachine_lists.id"), nullable=False)
    action = Column(String(20), nullable=False)
    before_json = Column(JSON, nullable=False, default=list)
    requested_json = Column(JSON, nullable=False, default=list)
    after_json = Column(JSON, nullable=False, default=list)
    actor_id = Column(Integer, ForeignKey("users.id"))
    created_at = Column(UTCDateTime(), default=utcnow)


class Export(Base):
    __tablename__ = "dealmachine_exports"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("dealmachine_runs.id"), nullable=False)
    status = Column(String(30), nullable=False, index=True)
    record_count = Column(Integer, nullable=False, default=0)
    credits_json = Column(JSON, nullable=False, default=dict)
    file_id = Column(Integer, ForeignKey("dealmachine_saved_files.id"))
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    completed_at = Column(UTCDateTime())


class SavedFile(Base):
    __tablename__ = "dealmachine_saved_files"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("dealmachine_runs.id"))
    kind = Column(String(40), nullable=False)
    relative_path = Column(String(1000), nullable=False)
    mime_type = Column(String(120), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    checksum = Column(String(64), nullable=False)
    created_at = Column(UTCDateTime(), default=utcnow)
