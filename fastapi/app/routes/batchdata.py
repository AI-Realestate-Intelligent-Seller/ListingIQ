# ruff: noqa: B008
import hashlib
import hmac
import json
import os
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..batchdata.client import ProviderError
from ..batchdata.config import (
    Configuration,
    ContactEnrichmentRequest,
    ProductRequest,
    ProductRunResponse,
)
from ..batchdata.models import (
    ApiCall,
    Membership,
    Property,
    Run,
    SavedFile,
    WebhookEvent,
)
from ..batchdata.service import (
    Service,
    UnsafeOperation,
    _rows,
    digest,
    operation_lock,
    property_table_row,
)
from ..batchdata.storage import (
    ArchiveCollisionError,
    StorageConfigurationError,
    configured_storage,
    json_bytes,
    read_with_fallback,
    webhook_archive_key,
)
from ..logger import get_logger, log_event, set_request_context
from ..models import PlatformAuditLog, User
from .auth import get_db
from .platform_admin import audit, require_platform_admin

router = APIRouter(dependencies=[Depends(require_platform_admin)])
webhook_router = APIRouter()
logger = get_logger(__name__)


def service(db: Session = Depends(get_db)):
    return Service(db)


@router.get("")
def overview(s: Service = Depends(service)):
    return s.summary()


@router.put("/config")
def save_config(
    payload: Configuration,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        with operation_lock(s.db):
            result = s.save_config(payload)
            audit(s.db, actor, "batchdata.configuration_saved", "integration", s.row.id)
            s.db.commit()
            return result
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None


class Action(BaseModel):
    reason: str = Field("", max_length=500)


def run_action(action: str, payload: Action, s: Service, actor: User):
    try:
        with operation_lock(s.db):
            if action == "test-connection":
                result = s.test_connection()
            else:
                if not s.summary()["monitoring_ready"]:
                    raise UnsafeOperation(
                        "Monitoring remains blocked until all PAYG monitoring prices and its spending cap are configured"
                    )
                result = {
                    "message": "Monitoring prices are configured; subscription execution remains disabled until an approved monitor plan exists"
                }
            audit(
                s.db,
                actor,
                f"batchdata.{action}",
                "integration",
                s.row.id,
                payload.reason or None,
            )
            s.db.commit()
            return result
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None
    except ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.post("/connection/test")
def test_connection(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("test-connection", payload, s, actor)


@router.post("/monitoring/validate")
def validate_monitoring(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("validate-monitoring", payload, s, actor)


def product_call(action: str, operation, payload, s: Service, actor: User):
    set_request_context(user_id=str(actor.id))
    fields = {
        "action": action,
        "actor_id": actor.id,
        "mode": s.api_mode,
        "confirmed": bool(getattr(payload, "confirmed", False)),
        "category_count": len(getattr(payload, "selected_categories", []) or []),
        "location_count": len(getattr(payload, "locations", []) or []),
        "property_count": len(getattr(payload, "property_ids", []) or []),
    }
    log_event(logger, "batchdata.product.requested", **fields)
    try:
        with operation_lock(s.db):
            log_event(logger, "batchdata.operation_lock.acquired", action=action)
            result = operation(payload)
            plan = result.get("call_plan", {})
            is_preview = result.get("status") == "Preview"
            log_event(
                logger,
                "batchdata.product.preview.completed"
                if is_preview
                else "batchdata.product.execution.completed",
                **fields,
                run_id=result.get("id"),
                status=result.get("status"),
                planned_provider_calls=len(plan.get("calls", [])),
                maximum_returned_rows=plan.get("maximum_returned_rows", 0),
                estimated_cost=result.get("estimated_cost", 0),
                actual_cost=result.get("actual_cost", 0),
                returned_records=result.get("returned_records", 0),
                unique_properties=result.get("unique_properties", 0),
                provider_calls_executed=0 if is_preview else len(result.get("provider_calls", [])),
                db_run_created=not is_preview,
                archive_writes=0 if is_preview else None,
            )
            audit(
                s.db,
                actor,
                f"batchdata.products.{action}",
                "integration",
                s.row.id,
                payload.reason or None,
            )
            s.db.commit()
            log_event(logger, "batchdata.audit.persisted", action=action, actor_id=actor.id)
            return result
    except UnsafeOperation as error:
        log_event(
            logger,
            "batchdata.product.rejected",
            level=__import__("logging").WARNING,
            **fields,
            reason=str(error),
        )
        raise HTTPException(409, str(error)) from None
    except ProviderError as error:
        log_event(
            logger,
            "batchdata.provider.failed",
            level=__import__("logging").ERROR,
            **fields,
            error=str(error),
        )
        raise HTTPException(502, str(error)) from None
    except ArchiveCollisionError as error:
        log_event(
            logger,
            "batchdata.archive.collision",
            level=__import__("logging").ERROR,
            **fields,
            error=str(error),
        )
        raise HTTPException(409, str(error)) from None
    except StorageConfigurationError as error:
        log_event(
            logger,
            "batchdata.archive.configuration_failed",
            level=__import__("logging").ERROR,
            **fields,
            error=str(error),
        )
        raise HTTPException(503, str(error)) from None


@router.post("/products/quick-lists/search", response_model=ProductRunResponse)
def search_quick_lists(
    payload: ProductRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return product_call("quick_lists.search", s.quick_lists, payload, s, actor)


@router.post("/products/basic-property/search", response_model=ProductRunResponse)
def search_basic_property(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return product_call("basic_property.search", s.basic_property, payload, s, actor)


@router.post("/products/listing-data/search", response_model=ProductRunResponse)
def search_listing_data(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return product_call("listing_data.search", s.listing_data, payload, s, actor)


@router.post("/products/pre-foreclosure/search", response_model=ProductRunResponse)
def search_pre_foreclosure(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return product_call("pre_foreclosure.search", s.pre_foreclosure, payload, s, actor)


@router.post("/products/contact-enrichment", response_model=ProductRunResponse)
def enrich_contacts(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return product_call("contact_enrichment", s.contact_enrichment, payload, s, actor)


def records(
    resource: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
    current_mode: bool = False,
):
    model = {
        "runs": Run,
        "properties": Property,
        "memberships": Membership,
        "api-calls": ApiCall,
        "webhooks": WebhookEvent,
        "saved-files": SavedFile,
        "audit": PlatformAuditLog,
    }[resource]
    query = s.db.query(model)
    if resource == "properties" and current_mode:
        query = query.filter(
            Property.provider == s.property_provider,
            Property.operational_copy["_quick_lists_run"].as_string().isnot(None),
        )
    if resource == "audit":
        query = query.filter(PlatformAuditLog.action.like("batchdata.%"))
    total = query.count()
    rows = query.order_by(model.id.desc()).offset(offset).limit(limit).all()
    items = []
    for row in rows:
        item = {
            column.name: getattr(row, column.name) for column in model.__table__.columns
        }
        if resource == "properties":
            item["table_data"] = property_table_row(row.immutable_provider_snapshot)
            item["stages"] = (row.operational_copy or {}).get("_stages", {})
            details = item["stages"].get("details", {}).get("data")
            if isinstance(details, dict):
                item["detail_table_data"] = property_table_row(details)
            item["contact_rows"] = []
            for result in item["stages"].get("contacts", {}).get("data", []) or []:
                for person in result.get("persons") or []:
                    name = person.get("name") or {}
                    item["contact_rows"].append(
                        {
                            "property_id": row.provider_property_id,
                            "name": person.get("fullName")
                            or name.get("full")
                            or " ".join(
                                str(name.get(key, "")) for key in ("first", "last")
                            ).strip(),
                            "phones": "; ".join(
                                str(phone.get("number", ""))
                                for phone in person.get("phones") or []
                            ),
                            "emails": "; ".join(
                                str(email.get("email", ""))
                                for email in person.get("emails") or []
                            ),
                        }
                    )
            item["quick_lists_saved"] = bool(
                (row.operational_copy or {}).get("_quick_lists_run")
            )
            item.pop("immutable_provider_snapshot", None)
            item.pop("operational_copy", None)
        items.append(item)
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/runs")
def run_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("runs", offset, limit, s)


@router.get("/properties/selectable-ids")
def selectable_property_ids(
    s: Service = Depends(service),
    stage: Literal["details", "contacts"] = "details",
):
    query = s.db.query(Property.id).filter(
        Property.provider == s.property_provider,
        Property.operational_copy["_quick_lists_run"].as_string().isnot(None),
        Property.operational_copy["_stages"][stage]["status"].as_string().is_(None),
    )
    if stage == "contacts":
        query = query.filter(
            or_(
                Property.skiptrace_status.is_(None),
                Property.skiptrace_status.notin_(["matched", "no_match"]),
            )
        )
    rows = query.order_by(Property.id.desc()).all()
    return {"property_ids": [row.id for row in rows]}


@router.get("/properties")
def property_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
    current_mode: bool = False,
):
    return records("properties", offset, limit, s, current_mode=current_mode)


@router.get("/memberships")
def membership_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("memberships", offset, limit, s)


@router.get("/api-calls")
def api_call_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("api-calls", offset, limit, s)


@router.get("/webhooks")
def webhook_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("webhooks", offset, limit, s)


@router.get("/saved-files")
def saved_file_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("saved-files", offset, limit, s)


@router.get("/audit")
def audit_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    s: Service = Depends(service),
):
    return records("audit", offset, limit, s)


@router.get("/properties/{property_id}")
def property_detail(property_id: int, s: Service = Depends(service)):
    row = s.db.get(Property, property_id)
    if row is None:
        raise HTTPException(404, "Property not found")
    return {
        "data": row.immutable_provider_snapshot,
        "stages": (row.operational_copy or {}).get("_stages", {}),
    }


@router.get("/saved-files/{file_id}/content")
def saved_file_content(file_id: int, s: Service = Depends(service)):
    row = s.db.get(SavedFile, file_id)
    if row is None:
        raise HTTPException(404, "Saved file not found")
    try:
        content = read_with_fallback(row.relative_path).decode("utf-8")
    except FileNotFoundError:
        raise HTTPException(404, "Saved file not available") from None
    except StorageConfigurationError as error:
        raise HTTPException(503, str(error)) from None
    properties = []
    suffix = os.path.splitext(row.relative_path)[1].lower()
    if suffix == ".json":
        value = json.loads(content)
        try:
            rows = value if isinstance(value, list) else _rows(value)
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                if raw.get("stage") == "contacts":
                    continue
                if raw.get("stage") == "details" and isinstance(raw.get("data"), dict):
                    summary = property_table_row(raw["data"])
                    summary["property_id"] = raw.get("source_provider_id")
                    properties.append(summary)
                else:
                    properties.append(property_table_row(raw))
        except (UnsafeOperation, AttributeError):
            pass
    elif suffix == ".csv":
        import csv
        import io

        properties = list(csv.DictReader(io.StringIO(content)))
    return {
        "name": os.path.basename(row.relative_path),
        "content": content,
        "properties": properties,
    }


def archive_webhook(db, clean, classification, event_key, provider_id, status):
    received_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    delivery_id = uuid.uuid4().hex
    metadata = {
        "classification": classification,
        "event_key": event_key,
        "provider_property_id": str(provider_id) if provider_id else None,
        "received_at": received_at.isoformat(),
        "processing_status": status,
        "payload_sha256": hashlib.sha256(json_bytes(clean)).hexdigest(),
    }
    storage = configured_storage()
    for name, value in (("payload", clean), ("metadata", metadata)):
        key = webhook_archive_key(
            os.getenv("BATCHDATA_API_MODE", "live"),
            classification,
            event_key,
            delivery_id,
            name,
            received_at,
        )
        content = json_bytes(value)
        storage.write(key, content)
        db.add(
            SavedFile(
                run_id=None,
                kind=f"webhook_{classification}_{name}"[:60],
                relative_path=key,
                size_bytes=len(content),
            )
        )


@webhook_router.post("/batchdata", status_code=202)
async def receive(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("BATCHDATA_WEBHOOK_SECRET", "")
    raw = await request.body()
    if len(raw) > 512_000:
        raise HTTPException(413, "Webhook payload exceeds 500 KB")
    supplied = request.headers.get("x-batchdata-signature", "").removeprefix("sha256=")
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if len(secret) < 32 or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Invalid webhook signature")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError()
    except (ValueError, TypeError, UnicodeDecodeError):
        raise HTTPException(422, "Expected a JSON object") from None

    def redact(value):
        if isinstance(value, dict):
            return {
                key: (
                    "[redacted]"
                    if key.lower()
                    in {"authorization", "token", "secret", "api_key", "apikey"}
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    clean = redact(payload)
    event_key = request.headers.get("x-batchdata-event-id")
    key = digest({"event_id": event_key}) if event_key else digest(clean)
    existing = db.query(WebhookEvent).filter_by(idempotency_key=key).one_or_none()
    if existing:
        try:
            archive_webhook(
                db,
                clean,
                "duplicate",
                key,
                existing.provider_property_id,
                "Duplicate",
            )
            db.commit()
        except Exception as error:
            db.rollback()
            raise HTTPException(503, "BatchData webhook archive failed") from error
        return {"accepted": True, "status": "Duplicate", "event_id": existing.id}

    provider_id = clean.get("_id") or clean.get("propertyId")
    if not provider_id and isinstance(clean.get("property"), dict):
        provider_id = clean["property"].get("_id")
    event = WebhookEvent(
        idempotency_key=key,
        provider_property_id=str(provider_id)[:160] if provider_id else None,
        status="Update Received",
        raw_payload=clean,
    )
    db.add(event)
    classification = "failed"
    if provider_id:
        prop = (
            db.query(Property)
            .filter_by(provider="batchdata", provider_property_id=str(provider_id))
            .one_or_none()
        )
        if not prop:
            snapshot = (
                clean.get("property")
                if isinstance(clean.get("property"), dict)
                else clean
            )
            prop = Property(
                provider_property_id=str(provider_id),
                immutable_provider_snapshot=snapshot,
                operational_copy=snapshot,
                skiptrace_status="waiting_for_skip_trace",
            )
            db.add(prop)
            event.status = "Waiting for Skip Trace"
            classification = "new"
        else:
            event.status = "Update Received"
            classification = "updated"
    else:
        event.status = "Failed"
        event.error_message = "Property ID is required"
    event.processed_at = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )
    try:
        archive_webhook(
            db,
            clean,
            classification,
            key,
            provider_id,
            event.status,
        )
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(503, "BatchData webhook archive failed") from error
    return {"accepted": True, "status": event.status, "event_id": event.id}
