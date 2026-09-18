from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)

from ..db import Base
from ..propertyradar.models import UTCDateTime, utcnow


class Run(Base):
    __tablename__ = "batchdata_runs"
    id = Column(String(64), primary_key=True)
    configuration_version = Column(Integer, nullable=False)
    status = Column(String(40), nullable=False, default="Draft", index=True)
    selected_categories = Column(JSON, nullable=False)
    call_plan = Column(JSON, nullable=False)
    estimated_cost = Column(Numeric(12, 4), nullable=False, default=0)
    actual_cost = Column(Numeric(12, 4), nullable=False, default=0)
    returned_records = Column(Integer, nullable=False, default=0)
    unique_properties = Column(Integer, nullable=False, default=0)
    duplicate_properties = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Property(Base):
    __tablename__ = "batchdata_properties"
    __table_args__ = (UniqueConstraint("provider", "provider_property_id"),)
    id = Column(Integer, primary_key=True)
    provider = Column(String(40), nullable=False, default="batchdata")
    provider_property_id = Column(String(160), nullable=False)
    address_hash = Column(String(160))
    apn = Column(String(160))
    normalized_address = Column(String(600))
    immutable_provider_snapshot = Column(JSON, nullable=False)
    operational_copy = Column(JSON, nullable=False)
    skiptrace_status = Column(String(40), nullable=False, default="not_requested")
    first_seen_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


@event.listens_for(Property.immutable_provider_snapshot, "set", retval=True)
def immutable_snapshot(target, value, oldvalue, initiator):
    if target.id is not None and oldvalue not in (None, value):
        raise ValueError("BatchData provider snapshots are immutable")
    return value


def _immutable_json(target, value, oldvalue, initiator):
    if target.id is not None and oldvalue not in (None, value):
        raise ValueError("Raw BatchData records are immutable")
    return value


class Membership(Base):
    __tablename__ = "batchdata_property_memberships"
    __table_args__ = (UniqueConstraint("property_id", "category"),)
    id = Column(Integer, primary_key=True)
    property_id = Column(Integer, ForeignKey("batchdata_properties.id"), nullable=False)
    category = Column(String(60), nullable=False)
    first_matched_at = Column(UTCDateTime(), default=utcnow)
    last_matched_at = Column(UTCDateTime(), default=utcnow)


class ApiCall(Base):
    __tablename__ = "batchdata_api_calls"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("batchdata_runs.id"))
    endpoint = Column(String(300), nullable=False)
    product = Column(String(60), nullable=False)
    status = Column(String(40), nullable=False, index=True)
    request_json = Column(JSON, nullable=False)
    response_json = Column(JSON)
    returned_records = Column(Integer, default=0, nullable=False)
    estimated_cost = Column(Numeric(12, 4), default=0, nullable=False)
    actual_cost = Column(Numeric(12, 4), default=0, nullable=False)
    request_id = Column(String(160))
    error_message = Column(Text)
    created_at = Column(UTCDateTime(), default=utcnow)


class WebhookEvent(Base):
    __tablename__ = "batchdata_webhook_events"
    id = Column(Integer, primary_key=True)
    idempotency_key = Column(String(64), unique=True, nullable=False)
    provider_property_id = Column(String(160), index=True)
    status = Column(String(40), nullable=False, index=True)
    raw_payload = Column(JSON, nullable=False)
    error_message = Column(Text)
    received_at = Column(UTCDateTime(), default=utcnow)
    processed_at = Column(UTCDateTime())


event.listen(
    ApiCall.response_json, "set", _immutable_json, retval=True, active_history=True
)
event.listen(WebhookEvent.raw_payload, "set", _immutable_json, retval=True)


class SavedFile(Base):
    __tablename__ = "batchdata_saved_files"
    id = Column(Integer, primary_key=True)
    run_id = Column(String(64), ForeignKey("batchdata_runs.id"))
    kind = Column(String(60), nullable=False)
    relative_path = Column(String(1000), nullable=False)
    size_bytes = Column(Integer, nullable=False, default=0)
    created_at = Column(UTCDateTime(), default=utcnow)


class Reservation(Base):
    __tablename__ = "batchdata_reservations"
    id = Column(Integer, primary_key=True)
    run_id = Column(
        String(64), ForeignKey("batchdata_runs.id"), unique=True, nullable=False
    )
    amount = Column(Numeric(12, 4), nullable=False)
    status = Column(String(40), nullable=False, default="reserved")
    created_at = Column(UTCDateTime(), default=utcnow)
    reconciled_at = Column(UTCDateTime())
