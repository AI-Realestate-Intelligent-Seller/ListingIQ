# ruff: noqa: B008
"""Platform-only, local data preparation and brokerage distribution endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..integration_data import service
from ..integration_data.models import CombinedProperty, DistributionRun
from ..models import User
from .auth import get_db
from .platform_admin import audit, require_platform_admin

router = APIRouter(dependencies=[Depends(require_platform_admin)])
Mode = Literal["live", "sandbox"]
LeadStatus = Literal["ready", "needs_review", "dnc"]


class CombineRequest(BaseModel):
    mode: Mode


class DivisionRule(BaseModel):
    brokerage_id: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=10000)
    categories: list[str] = Field(default_factory=list, max_items=100)
    lead_statuses: list[LeadStatus] = Field(default_factory=list, max_items=3)


class DistributionRequest(BaseModel):
    mode: Mode
    rules: list[DivisionRule] = Field(min_items=1, max_items=100)
    confirmed: bool = False
    reason: str = Field("", max_length=500)
    preview_hash: str = Field("", max_length=64)


@router.get("/summary")
def summary(mode: Mode = "live", db: Session = Depends(get_db)):
    rows = db.query(CombinedProperty).filter_by(mode=mode).all()
    return {
        "mode": mode,
        "providers": [
            {
                "key": key,
                "name": name,
                "count": len(service.source_records(db, key, mode)),
            }
            for key, name in service.PROVIDERS.items()
        ],
        "combined": len(rows),
        "unassigned": sum(row.brokerage_id is None for row in rows),
        "categories": sorted(
            {category for row in rows for category in row.categories_json}
        ),
        "lead_statuses": ["ready", "needs_review", "dnc"],
        "brokerages": service.brokerage_rows(db),
    }


@router.get("/sources/{provider}")
def source_data(
    provider: Literal["batchdata", "propertyradar", "dealmachine"],
    mode: Mode = "live",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = service.source_records(db, provider, mode)
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/sources/{provider}/{property_id}")
def source_property(
    provider: Literal["batchdata", "propertyradar", "dealmachine"],
    property_id: int,
    mode: Mode = "live",
    db: Session = Depends(get_db),
):
    record = next(
        (
            row
            for row in service.source_records(db, provider, mode)
            if row["source_record_id"] == property_id
        ),
        None,
    )
    if record is None:
        raise HTTPException(404, "Saved provider property not found in this data mode")
    model = {
        "batchdata": service.bd.Property,
        "propertyradar": service.pr.Property,
        "dealmachine": service.dm.Property,
    }[provider]
    row = db.get(model, property_id)
    if provider == "batchdata":
        data = row.immutable_provider_snapshot
    elif provider == "propertyradar":
        data = row.current_provider_payload
    else:
        data = row.operational_json
    result = {"data": data, "saved": record}
    if provider == "batchdata":
        result["stages"] = (row.operational_copy or {}).get("_stages", {})
    return result


@router.post("/combine")
def combine_data(
    payload: CombineRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_platform_admin),
):
    try:
        result = service.combine(db, payload.mode)
        audit(db, actor, "integration_data.combined", "inventory", payload.mode, result)
        db.commit()
        return result
    except (service.DataError, IntegrityError) as error:
        db.rollback()
        raise HTTPException(
            409,
            "Inventory changed; combine again"
            if isinstance(error, IntegrityError)
            else str(error),
        ) from None


@router.get("/combined")
def combined_data(
    mode: Mode = "live",
    category: str = "",
    lead_status: str = "",
    unassigned: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(CombinedProperty).filter_by(mode=mode)
    if unassigned:
        query = query.filter(CombinedProperty.brokerage_id.is_(None))
    if lead_status:
        query = query.filter_by(lead_status=lead_status)
    rows = query.order_by(CombinedProperty.id).all()
    if category:
        rows = [row for row in rows if category in row.categories_json]
    return {
        "items": [service.combined_value(row) for row in rows[offset : offset + limit]],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


@router.get("/combined/{property_id}")
def combined_property(property_id: int, db: Session = Depends(get_db)):
    row = db.get(CombinedProperty, property_id)
    if row is None:
        raise HTTPException(404, "Property not found")
    return service.combined_value(row)


@router.post("/distribution/preview")
def preview(payload: DistributionRequest, db: Session = Depends(get_db)):
    try:
        return service.preview_distribution(db, payload)
    except service.DataError as error:
        raise HTTPException(409, str(error)) from None


@router.post("/distribution/execute")
def execute(
    payload: DistributionRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_platform_admin),
):
    try:
        result = service.distribute(db, payload, actor.id)
        if not result.get("replayed"):
            audit(
                db,
                actor,
                "integration_data.distributed",
                "distribution",
                result["run_id"],
                {
                    "mode": payload.mode,
                    "reason": payload.reason,
                    "allocated": result["allocated"],
                    "leads_created": result["leads_created"],
                },
            )
        db.commit()
        return result
    except (service.DataError, IntegrityError) as error:
        db.rollback()
        raise HTTPException(
            409,
            "Inventory changed; preview again"
            if isinstance(error, IntegrityError)
            else str(error),
        ) from None


@router.get("/distribution/history")
def history(
    mode: Mode = "live",
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    query = db.query(DistributionRun).filter_by(mode=mode)
    rows = (
        query.order_by(DistributionRun.created_at.desc(), DistributionRun.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "items": [
            {
                "id": row.id,
                "created_at": row.created_at,
                "reason": row.reason,
                **row.result_json,
            }
            for row in rows
        ],
        "total": query.count(),
        "offset": offset,
        "limit": limit,
    }
