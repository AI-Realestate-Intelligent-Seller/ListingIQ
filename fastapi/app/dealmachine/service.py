import base64
import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func
from sqlalchemy.orm import Session

from .client import Client, ProviderError
from .config import (
    CATEGORIES,
    CategoryConfig,
    ContactEnrichmentRequest,
    DetailsRequest,
    Limits,
    SearchRequest,
    Settings,
)
from .models import (
    Category,
    ChangeEvent,
    ConfigVersion,
    ContactPoint,
    CreditUsage,
    EnrichmentJob,
    Execution,
    Export,
    Integration,
    ListMembershipEvent,
    Membership,
    MetadataCache,
    Person,
    Property,
    PropertyContact,
    ProviderList,
    Run,
    SavedFile,
    Snapshot,
    utcnow,
)

PROVIDER = "dealmachine"
BASE_URL = "https://api.v2.dealmachine.com/v1"
_locks = {}
_guard = threading.Lock()


class UnsafeOperation(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def redact(value):
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if key.lower()
                in {
                    "authorization",
                    "api_key",
                    "apikey",
                    "token",
                    "secret",
                    "encrypted_api_key",
                }
                else redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@contextmanager
def operation_lock(key):
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise UnsafeOperation("A DealMachine operation of this type is already running")
    try:
        yield
    finally:
        lock.release()


def _fernet():
    secret = os.getenv("DEALMACHINE_ENCRYPTION_KEY", "")
    if len(secret) < 32:
        return None
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt_key(value):
    cipher = _fernet()
    if not cipher:
        raise UnsafeOperation(
            "Set a 32+ character DEALMACHINE_ENCRYPTION_KEY before saving an API key"
        )
    return cipher.encrypt(value.encode()).decode()


def decrypt_key(value):
    if not value:
        return ""
    cipher = _fernet()
    if not cipher:
        raise UnsafeOperation("DEALMACHINE_ENCRYPTION_KEY is missing")
    try:
        return cipher.decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise UnsafeOperation(
            "The stored DealMachine credential cannot be decrypted"
        ) from error


def initialize(db: Session):
    # Importing models before create_all is required by this project's startup pattern.
    row = db.query(Integration).filter_by(provider=PROVIDER).one_or_none()
    if not row:
        settings = Settings().dict()
        row = Integration(provider=PROVIDER, enabled=False, settings_json=settings)
        db.add(row)
    for key, label in CATEGORIES:
        if not db.query(Category).filter_by(key=key).one_or_none():
            db.add(
                Category(
                    key=key, label=label, configuration_json=CategoryConfig().dict()
                )
            )
    db.commit()


class Service:
    def __init__(self, db: Session, provider_client=None):
        self.db = db
        self._provider_client = provider_client
        self.row = db.query(Integration).filter_by(provider=PROVIDER).one_or_none()
        if not self.row:
            initialize(db)
            self.row = db.query(Integration).filter_by(provider=PROVIDER).one()

    @property
    def settings(self):
        value = dict(self.row.settings_json or {})
        for legacy in (
            "api_key",
            "enabled",
            "automatic_enrichment",
            "ownership_change_reenrichment",
            "scheduler_enabled",
            "scheduler_interval_minutes",
        ):
            value.pop(legacy, None)
        return Settings(**value).dict()

    @property
    def api_key(self):
        return (
            decrypt_key(self.row.encrypted_api_key)
            if self.row.encrypted_api_key
            else os.getenv("DEALMACHINE_API_KEY", "")
        )

    @property
    def client(self):
        if self._provider_client is not None:
            return self._provider_client
        base = os.getenv("DEALMACHINE_BASE_URL", BASE_URL).rstrip("/")
        if (
            base != BASE_URL
            and os.getenv("DEALMACHINE_ALLOW_CUSTOM_BASE_URL") != "true"
        ):
            raise UnsafeOperation(
                "A custom DealMachine base URL requires DEALMACHINE_ALLOW_CUSTOM_BASE_URL=true on the backend"
            )
        return Client(base, self.api_key)

    def summary(self):
        account = self.row.account_json or {}
        usage = self.row.usage_json or {}
        plan = (
            account.get("data", account).get("plan", {})
            if isinstance(account, dict)
            else {}
        )
        usage_data = usage.get("data", usage) if isinstance(usage, dict) else {}
        categories = self.db.query(Category).order_by(Category.id).all()
        running_export = (
            self.db.query(Export)
            .filter(Export.status.in_(("running", "requested")))
            .first()
        )
        status = (
            "Ready"
            if self.row.connection_status == "connected"
            else "Needs setup"
            if self.row.connection_status == "disconnected"
            else "Error"
        )
        return {
            "provider": PROVIDER,
            "status": status,
            "enabled": self.row.enabled,
            "connection_status": self.row.connection_status,
            "credential": {
                "configured": bool(
                    self.row.encrypted_api_key or os.getenv("DEALMACHINE_API_KEY")
                ),
                "masked": self.masked_key(),
            },
            "encryption_configured": bool(_fernet()),
            "account": account,
            "usage": usage,
            "plan": plan.get("name")
            or usage_data.get("plan", {}).get("name")
            or "Unknown",
            "available_monthly_credits": usage_data.get("total_available")
            or usage_data.get("remaining")
            or 0,
            "property_credits_used": usage_data.get("properties_used")
            or usage_data.get("properties", {}).get("used", 0),
            "people_credits_used": usage_data.get("people_used")
            or usage_data.get("people", {}).get("used", 0),
            "last_tested_at": self.row.last_tested_at,
            "last_successful_sync_at": self.row.last_successful_sync_at,
            "next_scheduled_sync_at": self.row.next_scheduled_sync_at,
            "last_error": self.row.last_error,
            "settings": self.settings,
            "categories": [self.category_dict(item) for item in categories],
            "counts": {
                "properties": self.db.query(Property).count(),
                "runs": self.db.query(Run).count(),
                "queued": self.db.query(EnrichmentJob)
                .filter_by(status="pending")
                .count(),
            },
            "export_running": bool(running_export),
        }

    def masked_key(self):
        if self.row.key_hint:
            return f"dm_••••••••{self.row.key_hint}"
        key = os.getenv("DEALMACHINE_API_KEY", "")
        return f"dm_••••••••{key[-4:]}" if key else None

    def category_dict(self, row):
        return {
            "id": row.id,
            "key": row.key,
            "label": row.label,
            "enabled": row.enabled,
            "support_status": row.support_status,
            "configuration": row.configuration_json,
            "last_run_at": row.last_run_at,
            "next_run_at": row.next_run_at,
            "last_result_count": row.last_result_count,
            "new_match_count": row.new_match_count,
            "changed_record_count": row.changed_record_count,
            "credits_used": row.credits_used,
        }

    def save_settings(self, payload: Settings, actor_id):
        values = payload.dict()
        api_key = values.pop("api_key", None)
        if api_key:
            self.row.encrypted_api_key = encrypt_key(api_key)
            self.row.key_hint = api_key[-4:]
            self.row.connection_status = "disconnected"
        self.row.settings_json = values
        version = (self.db.query(func.max(ConfigVersion.version)).scalar() or 0) + 1
        self.db.add(
            ConfigVersion(
                version=version, configuration_json=redact(values), actor_id=actor_id
            )
        )
        self._write_json(f"config/configuration-v{version}.json", values)
        return self.summary()

    def save_category(self, key, payload: CategoryConfig):
        row = self.db.query(Category).filter_by(key=key).one_or_none()
        if not row:
            raise UnsafeOperation("Unknown ListingIQ category")
        values = payload.dict()
        if values["support_status"] == "available" and not values["filter_mappings"]:
            raise UnsafeOperation(
                "An available category requires a discovered DealMachine filter mapping"
            )
        for mapping in values["filter_mappings"]:
            cached = (
                self.db.query(MetadataCache)
                .filter_by(kind="filter", external_id=mapping["filter_id"])
                .one_or_none()
            )
            if not cached:
                raise UnsafeOperation(
                    f"Filter {mapping['filter_id']} must be loaded from DealMachine before it can be mapped"
                )
            allowed = (cached.metadata_json or {}).get("allowed_operators", [])
            if (
                mapping.get("operator")
                and allowed
                and mapping["operator"] not in allowed
            ):
                raise UnsafeOperation(
                    f"Operator {mapping['operator']} is not supported by {mapping['filter_id']}"
                )
        row.enabled = values["enabled"]
        row.support_status = values["support_status"]
        row.configuration_json = values
        return self.category_dict(row)

    def new_run(
        self, trigger, actor_id, categories, configuration, idempotency_key=None
    ):
        if idempotency_key:
            existing = (
                self.db.query(Run)
                .filter_by(idempotency_key=idempotency_key)
                .one_or_none()
            )
            if existing:
                raise UnsafeOperation(
                    f"Duplicate DealMachine job prevented; existing run {existing.id}"
                )
        run = Run(
            id=uuid.uuid4().hex,
            trigger_type=trigger,
            status="running",
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            categories_json=categories,
            configuration_snapshot=redact(configuration),
        )
        self.db.add(run)
        self.db.flush()
        self._write_run(run.id, configuration, {}, {}, {"status": "running"})
        return run

    def execute(
        self, run, endpoint, method, body, actor_id, call, category=None, paid=False
    ):
        self._check_request_budget()
        execution = Execution(
            run_id=run.id if run else None,
            sequence=self._next_sequence(run),
            endpoint=endpoint,
            method=method,
            category=category,
            actor_id=actor_id,
            request_json=redact(body or {}),
            requested_rows=int((body or {}).get("per_page", 0)),
            status="running",
        )
        self.db.add(execution)
        self.db.flush()
        try:
            response = call()
            execution.response_json = redact(response.data)
            execution.response_headers_json = response.headers
            execution.request_id = response.request_id
            execution.payload_hash = digest(response.data)
            credits = (
                response.data.get("credits", {})
                if isinstance(response.data, dict)
                else {}
            )
            execution.credits_json = credits
            data = (
                response.data.get("data", []) if isinstance(response.data, dict) else []
            )
            execution.returned_rows = len(data) if isinstance(data, list) else 0
            execution.status = "succeeded"
            execution.completed_at = utcnow()
            activity = response.data.get("activity_id") or response.data.get(
                "activityId"
            )
            execution.external_activity_id = str(activity)[:160] if activity else None
            if paid:
                self._record_credits(execution, credits)
            self.db.flush()
            self._write_execution(run.id if run else "standalone", execution)
            if run:
                self._write_run(
                    run.id,
                    run.configuration_snapshot,
                    execution.request_json,
                    execution.response_json,
                    run.summary_json or {"status": execution.status},
                )
            return response, execution
        except ProviderError as error:
            execution.status = "failed"
            execution.error_message = str(error)
            execution.request_id = error.request_id
            execution.completed_at = utcnow()
            self.row.last_error = str(error)
            self.db.flush()
            raise

    def test_connection(self, actor_id):
        run = self.new_run("manual", actor_id, [], {"endpoint": "account"})
        self.row.last_tested_at = utcnow()
        try:
            response, _ = self.execute(
                run, "/account", "GET", {}, actor_id, self.client.account
            )
            self.row.account_json = response.data
            self.row.connection_status = "connected"
            self.row.last_error = None
            run.status = "succeeded"
            run.completed_at = utcnow()
            return {
                "connection_status": "connected",
                "credential": self.masked_key(),
                "account": response.data,
                "run_id": run.id,
            }
        except ProviderError:
            self.row.connection_status = "error"
            run.status = "failed"
            run.completed_at = utcnow()
            raise

    def metadata(self, kind, refresh, actor_id):
        rows = (
            self.db.query(MetadataCache)
            .filter_by(kind=kind)
            .order_by(MetadataCache.name)
            .all()
        )
        if rows and not refresh:
            return {
                "items": [item.metadata_json for item in rows],
                "cached": True,
                "fetched_at": max(item.fetched_at for item in rows),
            }
        run = self.new_run("manual", actor_id, [], {"endpoint": kind})
        method = self.client.filters if kind == "filter" else self.client.fields
        response, _ = self.execute(
            run, f"/{kind}s?source_type=properties", "GET", {}, actor_id, method
        )
        items = response.data.get("data", response.data.get(f"{kind}s", []))
        if isinstance(items, dict):
            items = items.get("items", [])
        self.db.query(MetadataCache).filter_by(kind=kind).delete(
            synchronize_session=False
        )
        for item in items if isinstance(items, list) else []:
            external_id = (
                item.get("filter_id") or item.get("field_id") or item.get("id")
            )
            if external_id:
                self.db.add(
                    MetadataCache(
                        kind=kind,
                        external_id=str(external_id),
                        name=item.get("name") or item.get("label"),
                        group_name=item.get("group") or item.get("group_name"),
                        metadata_json=item,
                    )
                )
        run.status = "succeeded"
        run.completed_at = utcnow()
        return {
            "items": items if isinstance(items, list) else [],
            "cached": False,
            "run_id": run.id,
        }

    def usage(self, actor_id):
        run = self.new_run("manual", actor_id, [], {"endpoint": "usage"})
        response, _ = self.execute(
            run, "/usage", "GET", {}, actor_id, self.client.usage
        )
        self.row.usage_json = response.data
        run.status = "succeeded"
        run.completed_at = utcnow()
        return self.usage_state(response.data)

    def usage_state(self, raw=None):
        raw = raw or self.row.usage_json or {}
        data = raw.get("data", raw)
        available = int(data.get("total_available") or data.get("remaining") or 0)
        limits = Limits(**self.settings["limits"])
        used = self._cycle_usage()
        warning = available <= limits.minimum_remaining_credit_reserve
        blocked = (
            used["used"] >= limits.monthly_total_credit_cap
            or used["properties"] >= limits.monthly_property_credit_cap
            or used["people"] >= limits.monthly_people_credit_cap
        )
        return {
            "provider": raw,
            "internal": {**used, "limits": limits.dict()},
            "warning": warning,
            "blocked": blocked,
        }

    def count(self, payload: SearchRequest, actor_id):
        body = self.search_body(payload)
        body.pop("fields", None)
        body.pop("sort", None)
        body.pop("page", None)
        body.pop("per_page", None)
        body.pop("contact_audience", None)
        run = self.new_run(
            "manual", actor_id, payload.category_ids, body, payload.idempotency_key
        )
        response, execution = self.execute(
            run,
            "/properties/search/count",
            "POST",
            body,
            actor_id,
            lambda: self.client.property_count(body),
        )
        run.status = "succeeded"
        run.completed_at = utcnow()
        return self.result(response, execution, run)

    def estimate(self, payload: SearchRequest, actor_id):
        body = self.search_body(payload)
        body.update(
            {"estimate_cost": True, "anchor": "properties", "contact_audience": "none"}
        )
        run = self.new_run(
            "manual", actor_id, payload.category_ids, body, payload.idempotency_key
        )
        response, execution = self.execute(
            run,
            "/properties/search",
            "POST",
            body,
            actor_id,
            lambda: self.client.property_estimate(body),
        )
        run.status = "succeeded"
        run.completed_at = utcnow()
        return self.result(response, execution, run)

    def search(self, payload: SearchRequest, actor_id, trigger="manual"):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Paid property search requires confirmation and an audit reason"
            )
        # Usage and estimate are refreshed immediately before every paid search.
        # These calls are free and their own immutable executions are retained.
        self.usage(actor_id)
        estimate = self.estimate(
            payload.copy(
                update={"confirmed": False, "reason": "", "idempotency_key": None}
            ),
            actor_id,
        )
        estimated = (
            estimate.get("data", {})
            .get("estimated_credits", {})
            .get("this_page", payload.per_page)
        )
        body = self.search_body(payload)
        body.update({"anchor": "properties", "contact_audience": "none"})
        body.pop("estimate_cost", None)
        self._paid_guard(int(estimated), 0)
        run = self.new_run(
            trigger, actor_id, payload.category_ids, body, payload.idempotency_key
        )
        with operation_lock("property-search"):
            response, execution = self.execute(
                run,
                "/properties/search",
                "POST",
                body,
                actor_id,
                lambda: self.client.property_search(body),
                paid=True,
            )
            counts = self.ingest_properties(
                response.data, execution, run, payload.category_ids
            )
        warning = response.data.get("warning") or response.data.get("warnings")
        run.summary_json = {**counts, "provider_warning": warning}
        run.status = "partial" if warning else "succeeded"
        run.completed_at = utcnow()
        self.row.last_successful_sync_at = utcnow()
        self.row.last_error = (
            "Provider returned partial results; review before continuing"
            if warning
            else None
        )
        return {**self.result(response, execution, run), "processed": counts}

    def search_selected(self, payload: SearchRequest, actor_id):
        if len(payload.category_ids) <= 1:
            return self.search(payload, actor_id)
        # Categories are OR branches. DealMachine filters are AND-only, so each
        # category must be searched independently and merged by dm_property_id.
        results = [
            self.search_category(key, actor_id, payload.confirmed, payload.reason)
            for key in payload.category_ids
        ]
        totals = {
            key: sum(int(item.get("processed", {}).get(key, 0)) for item in results)
            for key in (
                "new",
                "existing",
                "changed",
                "unchanged",
                "new_memberships",
                "rejected",
                "queued",
                "local_duplicates",
            )
        }
        return {"category_runs": results, "processed": totals}

    def details(self, payload: DetailsRequest, actor_id):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Paid property details require confirmation and an audit reason"
            )
        properties = (
            self.db.query(Property)
            .filter(Property.provider_property_id.in_(payload.dm_property_ids))
            .all()
        )
        found = {item.provider_property_id for item in properties}
        missing = set(payload.dm_property_ids) - found
        if missing:
            raise UnsafeOperation(
                "Property details accepts only locally deduplicated DealMachine property IDs"
            )
        self.usage(actor_id)
        self._paid_guard(len(properties), 0)
        body = {
            "ids": payload.dm_property_ids,
            "fields": payload.fields,
            "contact_audience": "none",
        }
        run = self.new_run("manual", actor_id, [], body)
        response, execution = self.execute(
            run,
            "/properties/ids",
            "POST",
            body,
            actor_id,
            lambda: self.client.property_details(body),
            paid=True,
        )
        counts = self.ingest_details(
            response.data, execution, properties, include_contacts=False
        )
        run.summary_json = counts
        run.status = "succeeded"
        run.completed_at = utcnow()
        return {**self.result(response, execution, run), "processed": counts}

    def enrich_contacts(self, payload: ContactEnrichmentRequest, actor_id):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Contact enrichment requires confirmation and an audit reason"
            )
        properties = (
            self.db.query(Property)
            .filter(Property.provider_property_id.in_(payload.dm_property_ids))
            .all()
        )
        found = {item.provider_property_id for item in properties}
        missing = set(payload.dm_property_ids) - found
        if missing:
            raise UnsafeOperation(
                "Contact enrichment accepts only locally deduplicated DealMachine property IDs"
            )
        eligible = [
            item for item in properties if payload.force or not item.enriched_at
        ]
        if not eligible:
            raise UnsafeOperation("All selected properties are already enriched")
        if len(eligible) != len(properties) and not payload.force:
            raise UnsafeOperation(
                "One or more properties are already enriched; use Force Re-enrich with an audit reason"
            )
        self.usage(actor_id)
        self._paid_guard(len(eligible), len(eligible))
        body = {
            "ids": [item.provider_property_id for item in eligible],
            "fields": payload.fields,
            "contact_audience": payload.contact_audience,
        }
        run = self.new_run("manual", actor_id, [], body)
        jobs = []
        for prop in eligible:
            job = EnrichmentJob(
                property_id=prop.id,
                status="running",
                forced=payload.force,
                reason=payload.reason,
                actor_id=actor_id,
                started_at=utcnow(),
            )
            self.db.add(job)
            jobs.append(job)
            prop.enrichment_state = "running"
        try:
            with operation_lock("contact-enrichment"):
                response, execution = self.execute(
                    run,
                    "/properties/ids",
                    "POST",
                    body,
                    actor_id,
                    lambda: self.client.contact_enrichment(body),
                    paid=True,
                )
                counts = self.ingest_details(
                    response.data, execution, eligible, include_contacts=True
                )
            for job in jobs:
                job.status = "completed"
                job.completed_at = utcnow()
            run.summary_json = counts
            run.status = "succeeded"
            run.completed_at = utcnow()
            return {**self.result(response, execution, run), "processed": counts}
        except Exception:
            for job in jobs:
                job.status = "failed"
                job.error_message = "Provider request failed"
                job.completed_at = utcnow()
            raise

    def create_list(self, payload, actor_id):
        body = {
            "name": payload.name,
            "source_type": "properties",
            "filters": [item.dict() for item in payload.filters],
            "locations": [item.dict(exclude_none=True) for item in payload.locations],
        }
        if payload.record_ids:
            body = {
                "name": payload.name,
                "source_type": "properties",
                "record_ids": payload.record_ids,
            }
        run = self.new_run("manual", actor_id, payload.category_ids, body)
        response, execution = self.execute(
            run, "/lists", "POST", body, actor_id, lambda: self.client.create_list(body)
        )
        data = response.data.get("data", response.data)
        list_id = data.get("list_id") or data.get("id")
        if not list_id:
            raise ProviderError(
                "DealMachine list response did not contain list_id",
                request_id=response.request_id,
            )
        row = ProviderList(
            provider_list_id=str(list_id),
            name=payload.name,
            status=data.get("status", "building"),
            total_count=int(data.get("total_count", 0)),
            categories_json=payload.category_ids,
        )
        self.db.add(row)
        run.status = "succeeded"
        run.completed_at = utcnow()
        return {
            **self.result(response, execution, run),
            "list": {
                "list_id": list_id,
                "status": row.status,
                "total_count": row.total_count,
            },
        }

    def list_status(self, list_id, actor_id):
        row = (
            self.db.query(ProviderList)
            .filter_by(provider_list_id=list_id)
            .one_or_none()
        )

        if not row:
            raise UnsafeOperation("Unknown DealMachine list")
        if row.updated_at and utcnow() - row.updated_at < timedelta(seconds=3):
            return {
                "list_id": list_id,
                "status": row.status,
                "total_count": row.total_count,
                "cached": True,
            }
        run = self.new_run("manual", actor_id, [], {"list_id": list_id})
        response, execution = self.execute(
            run,
            f"/lists/{list_id}",
            "GET",
            {},
            actor_id,
            lambda: self.client.request("GET", f"/lists/{list_id}"),
        )
        data = response.data.get("data", response.data)
        row.status = data.get("status", row.status)
        row.total_count = int(data.get("total_count", row.total_count))
        row.updated_at = utcnow()
        if row.status == "completed":
            row.completed_at = row.completed_at or utcnow()
        run.status = "succeeded"
        run.completed_at = utcnow()
        return {**self.result(response, execution, run), "list": data}

    def list_items(self, list_id, payload, actor_id, remove=False):
        row = (
            self.db.query(ProviderList)
            .filter_by(provider_list_id=list_id)
            .one_or_none()
        )
        if not row:
            raise UnsafeOperation("Unknown DealMachine list")
        if remove and (not payload.confirmed or len(payload.reason.strip()) < 3):
            raise UnsafeOperation(
                "List removal requires confirmation and an audit reason"
            )
        body = {"ids": list(dict.fromkeys(payload.ids)), "id_type": payload.id_type}
        method = "DELETE" if remove else "POST"
        run = self.new_run("manual", actor_id, [], body)
        response, execution = self.execute(
            run,
            f"/lists/{list_id}/items",
            method,
            body,
            actor_id,
            lambda: self.client.request(
                method, f"/lists/{list_id}/items", body, retryable=False
            ),
        )
        before = {"total_count": row.total_count}
        data = response.data.get("data", response.data)
        row.total_count = int(data.get("total_count", row.total_count))
        self.db.add(
            ListMembershipEvent(
                list_id=row.id,
                action="remove" if remove else "add",
                before_json=before,
                requested_json=body,
                after_json={"total_count": row.total_count},
                actor_id=actor_id,
            )
        )
        run.status = "succeeded"
        run.completed_at = utcnow()
        return self.result(response, execution, run)

    def activity_search(self, payload, actor_id):
        body = payload.dict()
        run = self.new_run("manual", actor_id, [], body)
        response, execution = self.execute(
            run,
            "/activity/search",
            "POST",
            body,
            actor_id,
            lambda: self.client.activity_search(body),
        )
        run.status = "succeeded"
        run.completed_at = utcnow()
        return self.result(response, execution, run)

    def activity_detail(self, activity_id, actor_id):
        run = self.new_run("manual", actor_id, [], {"activity_id": activity_id})
        response, execution = self.execute(
            run,
            f"/activity/{activity_id}",
            "GET",
            {},
            actor_id,
            lambda: self.client.activity_detail(activity_id),
        )
        local = (
            self.db.query(Execution)
            .filter_by(external_activity_id=activity_id)
            .one_or_none()
        )
        run.status = "succeeded"
        run.completed_at = utcnow()
        result = self.result(response, execution, run)
        result["local_execution_id"] = local.id if local else None
        return result

    def export_properties(self, payload, actor_id):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Property export requires count, cost confirmation and an audit reason"
            )
        if (
            payload.expected_count > self.settings["limits"]["per_run_property_limit"]
            or payload.estimated_credits
            > self.settings["limits"]["per_run_property_limit"]
        ):
            raise UnsafeOperation(
                "Export exceeds the configured per-run property limit"
            )
        self.usage(actor_id)
        self._paid_guard(payload.estimated_credits, 0)
        with operation_lock("export"):
            if (
                self.db.query(Export)
                .filter(Export.status.in_(("requested", "running")))
                .first()
            ):
                raise UnsafeOperation("Only one DealMachine export may run at a time")
            body = self.search_body(payload)
            body.update({"anchor": "properties", "contact_audience": "none"})
            if payload.list_id:
                body["include_lists"] = {"property_list_ids": [payload.list_id]}
            run = self.new_run("manual", actor_id, payload.category_ids, body)
            export = Export(run_id=run.id, status="running")
            self.db.add(export)
            self.db.flush()
            try:
                response, execution = self.execute(
                    run,
                    "/properties/export",
                    "POST",
                    body,
                    actor_id,
                    lambda: self.client.property_export(body),
                    paid=True,
                )
                data = response.data.get("data", response.data)
                content = response.content
                if not content:
                    url = data.get("download_url") or data.get("url")
                    if url:
                        # Download URL comes only from the fixed provider response; it is never supplied by the browser.
                        from urllib.parse import urlparse

                        import requests

                        allowed = {
                            item.strip().lower()
                            for item in os.getenv(
                                "DEALMACHINE_EXPORT_DOWNLOAD_HOSTS",
                                "api.v2.dealmachine.com",
                            ).split(",")
                            if item.strip()
                        }
                        parsed = urlparse(url)
                        if (
                            parsed.scheme != "https"
                            or (parsed.hostname or "").lower() not in allowed
                        ):
                            raise UnsafeOperation(
                                "Provider export download host is not on the backend allowlist"
                            )
                        downloaded = requests.get(
                            url, timeout=(5, 150), allow_redirects=False
                        )
                        downloaded.raise_for_status()
                        content = downloaded.content
                if content:
                    target = self._root() / "exports" / f"{run.id}.zip"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    saved = SavedFile(
                        run_id=run.id,
                        kind="export",
                        relative_path=str(target.relative_to(self._root())),
                        mime_type=response.content_type,
                        size_bytes=len(content),
                        checksum=hashlib.sha256(content).hexdigest(),
                    )
                    self.db.add(saved)
                    self.db.flush()
                    export.file_id = saved.id
                export.status = "completed"
                export.record_count = int(
                    data.get("total_count", payload.expected_count)
                )
                export.credits_json = execution.credits_json
                export.completed_at = utcnow()
                run.status = "succeeded"
                run.completed_at = utcnow()
                return {
                    **self.result(response, execution, run),
                    "export_id": export.id,
                    "file_id": export.file_id,
                }
            except Exception:
                export.status = "failed"
                export.completed_at = utcnow()
                run.status = "failed"
                run.completed_at = utcnow()
                raise

    def history(
        self,
        offset=0,
        limit=50,
        endpoint=None,
        status=None,
        category=None,
        actor_id=None,
    ):
        query = self.db.query(Execution)
        if endpoint:
            query = query.filter(Execution.endpoint == endpoint)
        if status:
            query = query.filter(Execution.status == status)
        if category:
            query = query.filter(Execution.category == category)
        if actor_id:
            query = query.filter(Execution.actor_id == actor_id)
        total = query.count()
        rows = query.order_by(Execution.id.desc()).offset(offset).limit(limit).all()
        return {
            "items": [self.execution_dict(row, raw=False) for row in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def history_detail(self, run_id):
        run = self.db.query(Run).filter_by(id=run_id).one_or_none()
        if not run:
            raise UnsafeOperation("DealMachine run not found")
        executions = (
            self.db.query(Execution)
            .filter_by(run_id=run_id)
            .order_by(Execution.sequence)
            .all()
        )
        return {
            "run": {
                column.name: getattr(run, column.name)
                for column in run.__table__.columns
            },
            "executions": [self.execution_dict(row, raw=True) for row in executions],
        }

    def execution_dict(self, row, raw):
        keys = [column.name for column in row.__table__.columns]
        if not raw:
            keys = [
                key
                for key in keys
                if key not in {"request_json", "response_json", "response_headers_json"}
            ]
        return {key: getattr(row, key) for key in keys}

    def search_category(self, key, actor_id, confirmed, reason):
        category = self.db.query(Category).filter_by(key=key).one_or_none()
        if not category or not category.enabled:
            raise UnsafeOperation("Category is unavailable or disabled")
        config = CategoryConfig(**category.configuration_json)
        request = SearchRequest(
            category_ids=[key],
            filters=config.filter_mappings,
            locations=config.locations,
            fields=config.selected_fields,
            sort=config.sort,
            per_page=config.rows_per_fetch,
            confirmed=confirmed,
            reason=reason,
            idempotency_key=None,
        )
        return self.search(request, actor_id)

    def search_body(self, payload):
        return {
            "filters": [item.dict() for item in payload.filters],
            "locations": [item.dict(exclude_none=True) for item in payload.locations],
            "fields": payload.fields,
            "sort": payload.sort,
            "page": payload.page,
            "per_page": payload.per_page,
            "anchor": "properties",
            "contact_audience": "none",
        }

    def ingest_properties(self, raw, execution, run, category_keys):
        rows = raw.get("data", []) if isinstance(raw, dict) else []
        counts = {
            "new": 0,
            "existing": 0,
            "changed": 0,
            "unchanged": 0,
            "new_memberships": 0,
            "rejected": 0,
            "queued": 0,
            "local_duplicates": 0,
        }
        seen = set()
        categories = {
            item.key: item
            for item in self.db.query(Category)
            .filter(Category.key.in_(category_keys))
            .all()
        }
        matched = {key: set() for key in categories}
        for item in rows if isinstance(rows, list) else []:
            provider_id = item.get("dm_property_id")
            if not provider_id:
                counts["rejected"] += 1
                continue
            provider_id = str(provider_id)
            if provider_id in seen:
                counts["local_duplicates"] += 1
                continue
            seen.add(provider_id)
            payload_hash = digest(item)
            prop = (
                self.db.query(Property)
                .filter_by(provider=PROVIDER, provider_property_id=provider_id)
                .one_or_none()
            )
            if not prop:
                prop = Property(
                    provider_property_id=provider_id,
                    operational_json=item,
                    first_run_id=run.id,
                    last_run_id=run.id,
                    last_payload_hash=payload_hash,
                )
                self.db.add(prop)
                self.db.flush()
                counts["new"] += 1
                prop.enrichment_state = "not_queued"
            else:
                counts["existing"] += 1
                prop.last_seen_at = utcnow()
                prop.last_run_id = run.id
                if prop.last_payload_hash != payload_hash:
                    changed = sorted(set(prop.operational_json or {}) | set(item))
                    changed = [
                        key
                        for key in changed
                        if (prop.operational_json or {}).get(key) != item.get(key)
                    ]
                    self.db.add(
                        ChangeEvent(
                            property_id=prop.id,
                            run_id=run.id,
                            before_hash=prop.last_payload_hash,
                            after_hash=payload_hash,
                            changed_fields_json=changed,
                        )
                    )
                    prop.operational_json = item
                    prop.last_payload_hash = payload_hash
                    prop.change_detected_at = utcnow()
                    counts["changed"] += 1
                else:
                    counts["unchanged"] += 1
            self.db.add(
                Snapshot(
                    property_id=prop.id,
                    execution_id=execution.id,
                    snapshot_type="property",
                    payload_hash=payload_hash,
                    raw_payload=item,
                )
            )
            for key, category in categories.items():
                matched[key].add(prop.id)
                membership = (
                    self.db.query(Membership)
                    .filter_by(property_id=prop.id, category_id=category.id)
                    .one_or_none()
                )
                if not membership:
                    self.db.add(
                        Membership(property_id=prop.id, category_id=category.id)
                    )
                    counts["new_memberships"] += 1
                else:
                    membership.active = True
                    membership.last_matched_at = utcnow()
                    membership.no_longer_matched_at = None
        for key, category in categories.items():
            active = (
                self.db.query(Membership)
                .filter_by(category_id=category.id, active=True)
                .all()
            )
            for membership in active:
                if membership.property_id not in matched[key]:
                    membership.active = False
                    membership.no_longer_matched_at = utcnow()
            category.last_run_at = utcnow()
            category.last_result_count = len(matched[key])
            category.new_match_count = counts["new"]
            category.changed_record_count = counts["changed"]
        execution.returned_rows = len(rows)
        self._set_local_duplicates(execution, counts["local_duplicates"])
        return counts

    def ingest_details(self, raw, execution, properties, include_contacts):
        rows = raw.get("data", []) if isinstance(raw, dict) else []
        by_id = {item.provider_property_id: item for item in properties}
        contacts = 0
        for item in rows if isinstance(rows, list) else []:
            prop = by_id.get(str(item.get("dm_property_id")))
            if not prop:
                continue
            self.db.add(
                Snapshot(
                    property_id=prop.id,
                    execution_id=execution.id,
                    snapshot_type="details",
                    payload_hash=digest(item),
                    raw_payload=item,
                )
            )
            for contact in (
                (item.get("contacts", item.get("people", [])) or [])
                if include_contacts
                else []
            ):
                person_id = contact.get("dm_person_id")
                if not person_id:
                    continue
                person = (
                    self.db.query(Person)
                    .filter_by(provider=PROVIDER, provider_person_id=str(person_id))
                    .one_or_none()
                )
                if not person:
                    person = Person(
                        provider_person_id=str(person_id), current_json=contact
                    )
                    self.db.add(person)
                    self.db.flush()
                else:
                    person.current_json = contact
                if (
                    not self.db.query(PropertyContact)
                    .filter_by(property_id=prop.id, person_id=person.id)
                    .one_or_none()
                ):
                    self.db.add(
                        PropertyContact(property_id=prop.id, person_id=person.id)
                    )
                for phone in contact.get("phones", []) or []:
                    self._contact_point(person.id, "phone", phone.get("number"), phone)
                for email in contact.get("emails", []) or []:
                    self._contact_point(person.id, "email", email.get("address"), email)
                contacts += 1
            if include_contacts:
                prop.enriched_at = utcnow()
                prop.enrichment_state = "completed"
                self.db.query(EnrichmentJob).filter(
                    EnrichmentJob.property_id == prop.id,
                    EnrichmentJob.status.in_(("pending", "running")),
                ).update(
                    {"status": "completed", "completed_at": utcnow()},
                    synchronize_session=False,
                )
        return {"properties": len(rows), "contacts": contacts}

    def _contact_point(self, person_id, kind, value, metadata):
        if not value:
            return
        normalized = (
            "".join(char for char in value if char.isdigit())
            if kind == "phone"
            else value.strip().lower()
        )
        row = (
            self.db.query(ContactPoint)
            .filter_by(person_id=person_id, kind=kind, normalized_value=normalized)
            .one_or_none()
        )
        if not row:
            self.db.add(
                ContactPoint(
                    person_id=person_id,
                    kind=kind,
                    normalized_value=normalized,
                    display_value=value,
                    contact_type=metadata.get("type"),
                    do_not_call=metadata.get("do_not_call"),
                    metadata_json=metadata,
                )
            )
            self.db.flush()

    def _paid_guard(self, properties, people):
        if self.row.connection_status != "connected":
            raise UnsafeOperation(
                "Test the DealMachine connection successfully before a paid operation"
            )
        limits = Limits(**self.settings["limits"])
        current = self._cycle_usage()
        if (
            properties > limits.per_run_property_limit
            or people > limits.per_run_people_limit
        ):
            raise UnsafeOperation(
                "Requested rows exceed the configured per-run credit limit"
            )
        if (
            current["properties"] + properties > limits.monthly_property_credit_cap
            or current["people"] + people > limits.monthly_people_credit_cap
            or current["used"] + properties + people > limits.monthly_total_credit_cap
        ):
            raise UnsafeOperation(
                "Internal monthly DealMachine credit limit would be exceeded"
            )
        provider = self.usage_state().get("provider", {})
        data = provider.get("data", provider)
        available = int(data.get("total_available") or data.get("remaining") or 0)
        if (
            available
            and available - properties - people
            < limits.minimum_remaining_credit_reserve
        ):
            raise UnsafeOperation(
                "Provider balance would fall below the configured reserve"
            )

    def _cycle_usage(self):
        data = (self.row.usage_json or {}).get("data", self.row.usage_json or {})
        start = data.get("billing_cycle_start") or data.get("cycle_start")
        query = self.db.query(
            func.coalesce(func.sum(CreditUsage.used), 0),
            func.coalesce(func.sum(CreditUsage.properties), 0),
            func.coalesce(func.sum(CreditUsage.people), 0),
        )
        if start:
            try:
                query = query.filter(
                    CreditUsage.created_at
                    >= datetime.fromisoformat(start.replace("Z", "+00:00"))
                )
            except ValueError:
                pass
        used, properties, people = query.one()
        return {"used": int(used), "properties": int(properties), "people": int(people)}

    def _record_credits(self, execution, credits):
        data = (self.row.usage_json or {}).get("data", self.row.usage_json or {})

        def parse(value):
            if not value:
                return None
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None

        self.db.add(
            CreditUsage(
                execution_id=execution.id,
                cycle_start=parse(data.get("billing_cycle_start")),
                cycle_end=parse(data.get("billing_cycle_end")),
                used=int(credits.get("used", 0)),
                properties=int(credits.get("properties", 0)),
                people=int(credits.get("people", 0)),
                provider_deduplicated=int(credits.get("deduplicated", 0)),
            )
        )

    def _set_local_duplicates(self, execution, count):
        usage = (
            self.db.query(CreditUsage)
            .filter_by(execution_id=execution.id)
            .one_or_none()
        )
        if usage:
            usage.local_duplicates = count

    def _check_request_budget(self):
        limits = Limits(**self.settings["limits"])
        since = utcnow() - timedelta(days=1)
        if (
            self.db.query(Execution).filter(Execution.created_at >= since).count()
            >= limits.daily_request_limit
        ):
            raise UnsafeOperation("Internal daily DealMachine request limit reached")
        if (
            self.db.query(Execution)
            .filter(Execution.created_at >= utcnow() - timedelta(minutes=1))
            .count()
            >= 60
        ):
            raise UnsafeOperation(
                "DealMachine request queue reached the 60 requests/minute limit"
            )

    def _next_sequence(self, run):
        return (
            (
                self.db.query(func.max(Execution.sequence))
                .filter_by(run_id=run.id)
                .scalar()
                or 0
            )
            + 1
            if run
            else 1
        )

    def result(self, response, execution, run):
        return {
            "run_id": run.id,
            "execution_id": execution.id,
            "request_id": response.request_id,
            "status": execution.status,
            "data": response.data,
            "credits": execution.credits_json,
        }

    def _root(self):
        root = Path(
            os.getenv("DEALMACHINE_STORAGE_ROOT", "storage/integrations/dealmachine")
        ).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _write_json(self, relative, value):
        path = self._root() / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(redact(value), sort_keys=True, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def _write_run(self, run_id, configuration, request, response, summary):
        for name, value in (
            ("configuration", configuration),
            ("request", request),
            ("response", response),
            ("summary", summary),
        ):
            self._write_json(f"runs/{run_id}/{name}.json", value)

    def _write_execution(self, run_id, execution):
        date = utcnow().date().isoformat()
        payload = {
            "endpoint": execution.endpoint,
            "method": execution.method,
            "request": execution.request_json,
            "response": execution.response_json,
            "headers": execution.response_headers_json,
            "request_id": execution.request_id,
            "credits": execution.credits_json,
            "status": execution.status,
        }
        path = self._write_json(f"raw/{date}/{execution.id}.json", payload)
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        self.db.add(
            SavedFile(
                run_id=run_id
                if self.db.query(Run).filter_by(id=run_id).first()
                else None,
                kind="raw",
                relative_path=str(path.relative_to(self._root())),
                mime_type="application/json",
                size_bytes=path.stat().st_size,
                checksum=checksum,
            )
        )
