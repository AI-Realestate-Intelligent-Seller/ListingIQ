"""Platform-owned combined inventory and auditable brokerage allocations."""

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, UniqueConstraint

from ..db import Base
from ..propertyradar.models import UTCDateTime, utcnow


class CombinedProperty(Base):
    __tablename__ = "integration_combined_properties"
    __table_args__ = (UniqueConstraint("mode", "property_key"),)
    id = Column(Integer, primary_key=True)
    mode = Column(String(20), nullable=False, index=True)
    property_key = Column(String(500), nullable=False)
    data_json = Column(JSON, nullable=False, default=dict)
    sources_json = Column(JSON, nullable=False, default=list)
    categories_json = Column(JSON, nullable=False, default=list)
    lead_status = Column(String(30), nullable=False, default="needs_review")
    content_hash = Column(String(64), nullable=False)
    brokerage_id = Column(String(255), index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"))
    distribution_run_id = Column(
        String(64), ForeignKey("integration_distribution_runs.id")
    )
    created_at = Column(UTCDateTime(), default=utcnow)
    updated_at = Column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class DistributionRun(Base):
    __tablename__ = "integration_distribution_runs"
    id = Column(String(64), primary_key=True)
    mode = Column(String(20), nullable=False)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    preview_hash = Column(String(64), nullable=False, unique=True)
    reason = Column(String(500), nullable=False)
    plan_json = Column(JSON, nullable=False)
    result_json = Column(JSON, nullable=False)
    created_at = Column(UTCDateTime(), default=utcnow)
