# ruff: noqa: B008 -- FastAPI dependency injection follows existing route conventions.
"""Internal management and independently authenticated provider delivery routes."""

import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, or_
from sqlalchemy.orm import Session

from ..models import PlatformAuditLog, User
from ..propertyradar.client import ProviderError
from ..propertyradar.config import (
    Configuration,
    ContactEnrichmentRequest,
    ContactEnrichmentResponse,
    PropertyDetailsRequest,
    PropertyDetailsResponse,
    PropertySearchRequest,
    PropertySearchResponse,
)
from ..propertyradar.models import (
    ProviderList,
    SkiptraceJob,
    Usage,
    WebhookEvent,
    utcnow,
)
from ..propertyradar.service import (
    Service,
    UnsafeOperation,
    digest,
    operation_lock,
)
from .auth import get_db
from .platform_admin import audit, require_platform_admin

router = APIRouter(dependencies=[Depends(require_platform_admin)])
index_router = APIRouter(dependencies=[Depends(require_platform_admin)])
webhook_router = APIRouter()


def service(db: Session = Depends(get_db)):
    # Seeded by migration/startup. Do not write on read-only management requests.
    return Service(db)


@index_router.get("")
def integrations(s: Service = Depends(service)):
    from ..batchdata.service import Service as BatchDataService
    from ..dealmachine.service import Service as DealMachineService

    return {
        "integrations": [
            s.summary(),
            BatchDataService(s.db).summary(),
            DealMachineService(s.db).summary(),
        ]
    }


@router.get("")
def overview(s: Service = Depends(service)):
    return s.summary()


@router.put("/config")
def config(
    payload: Configuration,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    values = json.loads(payload.json())

    def save_values():
        row = s.row
        if (
            values["billing_cycle_start"]
            != row.configuration_json.get("billing_cycle_start")
            and s.db.query(Usage).filter(Usage.operation_key.isnot(None)).first()
        ):
            raise HTTPException(
                409,
                "Billing-cycle anchor is locked after the first purchase; reconcile ledger before changing it",
            )
        row.enabled = values["enabled"]
        row.configuration_json = values
        audit(s.db, actor, "propertyradar.configuration_saved", "integration", row.id)
        s.db.commit()

    # An enable/disable-only update can interrupt work between provider requests.
    # All cost or criteria changes acquire the same cross-process purchase guard.
    if values == {**s.config, "enabled": values["enabled"]}:
        row = s.row
        row.enabled = values["enabled"]
        audit(s.db, actor, "propertyradar.processing_toggled", "integration", row.id)
        s.db.commit()
    else:
        try:
            with operation_lock(s.db):
                save_values()
        except UnsafeOperation as error:
            raise HTTPException(409, str(error)) from None
    return s.summary()


def data_call(action: str, operation, payload, s: Service, actor: User):
    try:
        with operation_lock(s.db):
            result = operation(payload)
            audit(
                s.db,
                actor,
                f"propertyradar.data.{action}",
                "integration",
                s.row.id,
                getattr(payload, "reason", None),
            )
            s.db.commit()
            return result
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None
    except ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.post("/properties/search", response_model=PropertySearchResponse)
def search_properties(
    payload: PropertySearchRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return data_call("properties.search", s.search_properties, payload, s, actor)


@router.post("/properties/details", response_model=PropertyDetailsResponse)
def fetch_property_details(
    payload: PropertyDetailsRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return data_call("properties.details", s.fetch_property_details, payload, s, actor)


@router.post("/contacts/enrich", response_model=ContactEnrichmentResponse)
def enrich_contacts(
    payload: ContactEnrichmentRequest,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return data_call("contacts.enrich", s.enrich_contacts, payload, s, actor)


class Action(BaseModel):
    preview_id: str = Field("", max_length=64)
    confirmed: bool = False


def run_action(action: str, payload: Action, s: Service, actor: User):
    try:
        with operation_lock(s.db):
            if action == "enable-monitoring" and not payload.confirmed:
                raise UnsafeOperation("Explicit administrator confirmation is required")
            if action == "test-connection":
                try:
                    s.call(
                        "POST",
                        "/v1/properties",
                        {"Fields": "RadarID", "Limit": 1, "Purchase": 0, "Start": 0},
                        {"Criteria": [{"name": "RadarID", "value": ["P8A0E18D"]}]},
                        "connection",
                        check_enabled=False,
                    )
                    s.row.connection_status = "connected"
                    s.row.last_successful_connection_at = utcnow()
                    s.row.last_error = None
                except ProviderError as error:
                    s.row.connection_status = "error"
                    s.row.last_error = str(error)
                    raise
                finally:
                    s.row.last_connection_test_at = utcnow()
                    s.db.commit()
                result = {"message": "PropertyRadar connection verified"}
            elif action == "prepare-lists":
                result = s.prepare_lists()
            elif action in ("validate-monitoring", "refresh-lists"):
                result = s.validate_monitoring()
            elif action == "register-webhook":
                result = s.register_webhook()
            elif action == "enable-monitoring":
                result = s.monitoring(True, payload.preview_id)
            elif action == "pause-monitoring":
                result = s.monitoring(False)
            else:
                s.db.query(WebhookEvent).filter(
                    WebhookEvent.processing_status.in_(["budget_deferred", "error"])
                ).update(
                    {
                        WebhookEvent.processing_status: "pending",
                        WebhookEvent.error_message: None,
                    }
                )
                s.db.commit()
                result = {"message": "Deferred/error events queued for guarded retry"}
            audit(s.db, actor, f"propertyradar.{action}", "integration", s.row.id)
            s.db.commit()
            return result
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None
    except ProviderError as error:
        raise HTTPException(502, str(error)) from None
    except (ValueError, KeyError, TypeError):
        raise HTTPException(
            502, "Unexpected provider response; inspect API logs before retrying"
        ) from None


@router.post("/connection/test")
def test_connection(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("test-connection", payload, s, actor)


@router.post("/lists/prepare")
def prepare_lists(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("prepare-lists", payload, s, actor)


@router.post("/monitoring/validate")
def validate_monitoring(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("validate-monitoring", payload, s, actor)


@router.post("/lists/refresh")
def refresh_lists(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("refresh-lists", payload, s, actor)


@router.post("/webhook/register")
def register_webhook(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("register-webhook", payload, s, actor)


@router.post("/monitoring/enable")
def enable_monitoring(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("enable-monitoring", payload, s, actor)


@router.post("/monitoring/pause")
def pause_monitoring(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("pause-monitoring", payload, s, actor)


@router.post("/events/retry")
def retry_events(
    payload: Action,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    return run_action("retry-events", payload, s, actor)


RESOURCE_SEARCH_COLUMNS = {
    "radar_id",
    "provider_list_id",
    "list_name",
    "trigger_type",
    "usage_type",
    "endpoint",
    "action",
    "target_type",
    "detail",
}


def records(
    resource: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    model = {
        "lists": ProviderList,
        "events": WebhookEvent,
        "usage": Usage,
        "api-logs": Usage,
        "skiptrace-jobs": SkiptraceJob,
        "audit": PlatformAuditLog,
    }[resource]
    query = s.db.query(model)
    if resource == "audit":
        # Scope the audit trail to this integration only; every action here
        # is logged with an "propertyradar." action prefix.
        query = query.filter(PlatformAuditLog.action.like("propertyradar.%"))
    if q:
        query = query.filter(
            or_(
                *[
                    cast(c, String).ilike(f"%{q}%")
                    for c in model.__table__.columns
                    if c.name in RESOURCE_SEARCH_COLUMNS
                ]
            )
        )
    if status:
        field = (
            getattr(model, "processing_status", None)
            if model is WebhookEvent
            else getattr(model, "status", None)
        )
        if field is not None:
            query = query.filter(field == status)
    total = query.count()
    rows = query.order_by(model.id.desc()).offset(offset).limit(limit).all()
    items = [
        {
            c.name: getattr(row, c.name)
            for c in model.__table__.columns
            if c.name != "response_payload"
        }
        for row in rows
    ]
    if resource == "events":
        for item in items:
            job = s.db.query(SkiptraceJob).filter_by(radar_id=item["radar_id"]).first()
            item["skiptrace_status"] = job.status if job else "not_requested"
    if resource == "lists":
        from ..propertyradar.models import Category

        for item in items:
            item["category"] = s.db.get(Category, item["category_id"]).label
    if resource == "audit":
        actors = {
            u.id: u.email
            for u in s.db.query(User)
            .filter(User.id.in_({row.actor_id for row in rows if row.actor_id}))
            .all()
        }
        for item in items:
            item["actor_email"] = actors.get(item["actor_id"], "System")
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@router.get("/lists")
def list_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("lists", offset, limit, q, status, s)


@router.get("/events")
def event_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("events", offset, limit, q, status, s)


@router.get("/usage")
def usage_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("usage", offset, limit, q, status, s)


@router.get("/api-logs")
def api_log_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("api-logs", offset, limit, q, status, s)


@router.get("/skiptrace-jobs")
def skiptrace_job_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("skiptrace-jobs", offset, limit, q, status, s)


@router.get("/audit")
def audit_records(
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str = Query("", max_length=100),
    status: str = Query("", max_length=40),
    s: Service = Depends(service),
):
    return records("audit", offset, limit, q, status, s)


@router.post("/lists/{list_id}/pause")
def pause_list(
    list_id: int,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        with operation_lock(s.db):
            result = s.pause_list(list_id)
            audit(s.db, actor, "propertyradar.list_paused", "provider_list", list_id)
            s.db.commit()
            return result
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None
    except ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.post("/skiptrace-jobs/{job_id}/retry")
def retry(
    job_id: int,
    s: Service = Depends(service),
    actor: User = Depends(require_platform_admin),
):
    try:
        with operation_lock(s.db):
            job = s.db.get(SkiptraceJob, job_id)
            if not job:
                raise HTTPException(404, "Job not found")
            if job.status == "completed":
                raise HTTPException(409, "Skip tracing already completed")
            job.status = "pending"
            job.error_message = None
            audit(s.db, actor, "propertyradar.skiptrace_retry", "skiptrace_job", job.id)
            s.db.commit()
        return {"message": "Job queued; all purchase safety checks still apply"}
    except UnsafeOperation as error:
        raise HTTPException(409, str(error)) from None


@webhook_router.post("/propertyradar", status_code=202)
async def receive(request: Request, db: Session = Depends(get_db)):
    secret = os.getenv("PROPERTYRADAR_WEBHOOK_SECRET", "")
    supplied = request.headers.get("authorization", "")
    if len(secret) < 32 or not hmac.compare_digest(
        supplied.encode(), f"Bearer {secret}".encode()
    ):
        raise HTTPException(401, "Invalid webhook authentication")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 256000:
            db.add(
                WebhookEvent(
                    payload_hash=digest("oversized"),
                    raw_payload={"invalid": "Payload exceeds 256 KB"},
                    processing_status="invalid",
                    error_message="Payload exceeds 256 KB",
                )
            )
            db.commit()
            return {"accepted": True, "status": "invalid"}
    error = None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError()
    except (ValueError, TypeError, UnicodeDecodeError):
        payload = {"invalid_json": bytes(raw).decode("utf-8", errors="replace")}
        error = "Expected a JSON object"

    # Payload may contain arbitrary keys, but never persist echoed authentication secrets.
    def redact(value):
        if isinstance(value, dict):
            return {
                k: (
                    "[redacted]"
                    if k.lower()
                    in ("authorization", "secret", "token", "x-radar-access-token")
                    else redact(v)
                )
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [redact(v) for v in value]
        if isinstance(value, str):
            for credential in (secret, os.getenv("PROPERTYRADAR_API_TOKEN", "")):
                if credential:
                    value = value.replace(credential, "[redacted]")
        return value

    payload = redact(payload)
    radar = payload.get("RadarID")
    trigger = payload.get("TriggerType")
    if (
        not isinstance(radar, str)
        or not re_valid_radar(radar)
        or trigger not in ("New Match", "Change", "New Record")
    ):
        error = error or "RadarID and supported TriggerType are required"
    test = isinstance(radar, str) and radar.startswith("TEST-")
    # Reserved TEST- identifiers NEVER reach the provider, even if delivered in production.
    status = "invalid" if error else ("test" if test else "pending")

    def string_field(key, size):
        value = payload.get(key)
        return str(value)[:size] if value is not None else None

    hashed = digest(payload)
    repeated = (
        db.query(WebhookEvent.id).filter_by(payload_hash=hashed).first() is not None
    )
    event = WebhookEvent(
        radar_id=string_field("RadarID", 100),
        provider_list_id=string_field("ListID", 80),
        list_name=string_field("ListName", 200),
        trigger_type=string_field("TriggerType", 40),
        new_record_type=string_field("NewRecordType", 100),
        change_1=string_field("Change1", 4000),
        change_2=string_field("Change2", 4000),
        change_3=string_field("Change3", 4000),
        payload_hash=hashed,
        raw_payload=payload,
        processing_status=status,
        is_retry_duplicate=repeated,
        is_test=test,
        error_message=error,
    )
    db.add(event)
    db.add(
        Usage(
            usage_type="webhook_delivery",
            endpoint="POST /webhooks/propertyradar",
            result_count=1,
        )
    )
    db.commit()
    return {"accepted": True, "event_id": event.id, "status": status}


def re_valid_radar(value):
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value))
