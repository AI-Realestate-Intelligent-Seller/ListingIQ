import csv
import hashlib
import io
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, update

from ..logger import get_logger, log_event
from ..propertyradar.models import IntegrationConfig, utcnow
from .client import Client
from .config import (
    CATEGORIES,
    FORECLOSURE_CATEGORIES,
    LISTING_CATEGORIES,
    Configuration,
    ContactEnrichmentRequest,
    ProductRequest,
)
from .models import (
    ApiCall,
    Membership,
    Property,
    Reservation,
    Run,
    SavedFile,
    WebhookEvent,
)
from .storage import configured_storage, run_archive_key

logger = get_logger(__name__)


class UnsafeOperation(ValueError):
    pass


@contextmanager
def operation_lock(db):
    token = uuid.uuid4().hex
    changed = db.execute(
        update(IntegrationConfig)
        .where(
            IntegrationConfig.provider == "batchdata",
            IntegrationConfig.lock_token.is_(None),
        )
        .values(lock_token=token, lock_started_at=utcnow())
    ).rowcount
    db.commit()
    if changed != 1:
        raise UnsafeOperation(
            "Another BatchData operation is running; reconcile it before retrying"
        )
    try:
        yield
    finally:
        db.rollback()
        db.execute(
            update(IntegrationConfig)
            .where(
                IntegrationConfig.provider == "batchdata",
                IntegrationConfig.lock_token == token,
            )
            .values(lock_token=None, lock_started_at=None)
        )
        db.commit()


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def initialize(db):
    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    db.execute(
        insert(IntegrationConfig)
        .values(
            provider="batchdata", configuration_json=json.loads(Configuration().json())
        )
        .on_conflict_do_nothing(index_elements=["provider"])
    )
    db.commit()
    row = db.query(IntegrationConfig).filter_by(provider="batchdata").one()
    # Early local seeds included Short Sale before the final provider decision
    # marked it unsupported. Normalize that saved draft instead of letting one
    # stale selection make the entire Integrations page fail to render.
    defaults = json.loads(Configuration().json())
    values = {**defaults, **(row.configuration_json or {})}
    values["selectedCategories"] = [
        key for key in values.get("selectedCategories", []) if key != "short_sale"
    ]
    normalized = json.loads(Configuration(**values).json())
    if normalized != row.configuration_json:
        row.configuration_json = normalized
        db.commit()
    return row


def _rows(payload):
    candidates = [payload.get("results"), payload.get("properties")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([data.get("results"), data.get("properties")])
    for value in candidates:
        if isinstance(value, dict):
            value = value.get("properties")
        if isinstance(value, list):
            if any(not isinstance(row, dict) for row in value):
                raise UnsafeOperation("BatchData returned malformed property rows")
            return value
    raise UnsafeOperation(
        "BatchData response does not contain a property results array"
    )


def property_table_row(raw):
    def section(key):
        value = raw.get(key)
        return value if isinstance(value, dict) else {}

    address, owner = section("address"), section("owner")
    building, valuation, listing = (
        section("building"),
        section("valuation"),
        section("listing"),
    )
    return {
        "property_id": raw.get("_id") or raw.get("id"),
        "address": address.get("formatted")
        or ", ".join(
            str(address[k])
            for k in ("street", "city", "state", "zip")
            if address.get(k)
        ),
        "owner": owner.get("fullName"),
        "property_type": section("general").get("propertyTypeDetail"),
        "beds": building.get("bedroomCount"),
        "baths": building.get("calculatedBathroomCount"),
        "area_sqft": building.get("livingAreaSquareFeet")
        or building.get("totalBuildingAreaSquareFeet"),
        "estimated_value": valuation.get("estimatedValue"),
        "equity_percent": valuation.get("equityPercent"),
        "listing_status": listing.get("status"),
        "listing_price": listing.get("price"),
    }


def _location_calls(calls, locations, rows_per_category):
    if len(locations) > rows_per_category:
        raise UnsafeOperation(
            "Results per category must be at least the number of locations"
        )
    expanded = []
    for call in calls:
        unit = Decimal(str(call["estimated_cost"])) / rows_per_category
        for index, location in enumerate(locations):
            take = rows_per_category // len(locations) + int(
                index < rows_per_category % len(locations)
            )
            entry = json.loads(json.dumps(call))
            entry["request"]["searchCriteria"]["query"] = location
            entry["request"]["options"]["take"] = take
            entry["maximum_rows"] = take
            entry["estimated_cost"] = float(unit * take)
            entry["location"] = location
            expanded.append(entry)
    return expanded


def _matched(payload) -> bool:
    data = payload.get("data")
    if isinstance(data, dict):
        return bool(
            data.get("persons")
            or data.get("people")
            or data.get("phones")
            or data.get("emails")
        )
    return bool(
        payload.get("persons")
        or payload.get("people")
        or payload.get("phones")
        or payload.get("emails")
    )


def _redact(value):
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if key.lower()
                in {"token", "authorization", "api_key", "apikey", "secret"}
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class Service:
    @property
    def api_mode(self):
        return os.getenv("BATCHDATA_API_MODE", "live")

    @property
    def property_provider(self):
        return "batchdata_sandbox" if self.api_mode == "sandbox" else "batchdata"

    def __init__(self, db, client=None):
        self.db = db
        self.client = client or Client()

    @property
    def row(self):
        row = (
            self.db.query(IntegrationConfig)
            .filter_by(provider="batchdata")
            .one_or_none()
        )
        return row or initialize(self.db)

    @property
    def config(self):
        return json.loads(Configuration(**self.row.configuration_json).json())

    def summary(self):
        cfg = self.config
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        spend = (
            self.db.query(func.coalesce(func.sum(ApiCall.actual_cost), 0))
            .filter(ApiCall.created_at >= start)
            .scalar()
        )
        skip_spend = (
            self.db.query(func.coalesce(func.sum(ApiCall.actual_cost), 0))
            .filter(
                ApiCall.created_at >= start, ApiCall.product == "contact_enrichment"
            )
            .scalar()
        )
        skip_count = (
            self.db.query(func.coalesce(func.sum(ApiCall.returned_records), 0))
            .filter(
                ApiCall.created_at >= start, ApiCall.product == "contact_enrichment"
            )
            .scalar()
        )
        last_call = (
            self.db.query(ApiCall)
            .filter(ApiCall.status == "Completed")
            .order_by(ApiCall.id.desc())
            .first()
        )
        last_webhook = (
            self.db.query(WebhookEvent).order_by(WebhookEvent.id.desc()).first()
        )
        monitoring_ready = all(
            cfg[key] is not None
            for key in (
                "monitorNewMatchUnitCost",
                "monitorUpdateUnitCost",
                "monitorMonthlyFixedCost",
                "monitorMonthlySpendCap",
            )
        )
        status = (
            "Error"
            if self.row.last_error
            else ("Active" if self.row.enabled else "Disabled")
        )
        return {
            "status": status,
            "enabled": self.row.enabled,
            "connection_status": self.row.connection_status,
            "token_configured": bool(os.getenv("BATCHDATA_API_TOKEN")),
            "api_mode": os.getenv("BATCHDATA_API_MODE", "live"),
            "webhook_secret_configured": len(os.getenv("BATCHDATA_WEBHOOK_SECRET", ""))
            >= 32,
            "last_successful_call": last_call.created_at if last_call else None,
            "last_webhook": last_webhook.received_at if last_webhook else None,
            "last_error": self.row.last_error,
            "config": cfg,
            "categories": [
                {"key": key, "label": label, "supported": key != "short_sale"}
                for key, label in CATEGORIES.items()
            ],
            "usage": {
                "monthly_spend": float(spend),
                "monthly_cap": cfg["monthlySpendCap"],
                "skip_trace_spend": float(skip_spend),
                "skip_trace_cap": cfg["skipTraceSpendCap"],
                "skip_trace_matches": int(skip_count),
                "skip_trace_limit": cfg["monthlySkipTraceLimit"],
                "allow_overage": False,
            },
            "metrics": {
                "properties": self.db.query(Property).count(),
                "runs": self.db.query(Run).count(),
                "webhooks": self.db.query(WebhookEvent).count(),
            },
            "monitoring_ready": monitoring_ready,
            "monitoring_status": "Ready to configure"
            if monitoring_ready
            else "Blocked: PAYG prices not confirmed",
        }

    def save_config(self, payload: Configuration):
        current = self.config
        values = json.loads(payload.json())
        changed = {
            key: value for key, value in values.items() if key != "configurationVersion"
        } != {
            key: value
            for key, value in current.items()
            if key != "configurationVersion"
        }
        values["configurationVersion"] = current["configurationVersion"] + (
            1 if changed else 0
        )
        self.row.enabled = values["enabled"]
        self.row.configuration_json = values
        if changed:
            self.db.query(Run).filter(
                Run.status.in_(["Draft", "Validated", "Approved", "Planned"])
            ).update({Run.status: "Requires Validation"})
        self.db.commit()
        return self.summary()

    def call_plan(self):
        cfg = self.config
        return self._product_plan(
            "quick_lists",
            ProductRequest(
                selected_categories=cfg["selectedCategories"],
                locations=cfg["locations"],
                combination=cfg["combination"],
                rows_per_category=cfg["rowsPerCategory"],
            ),
        )

    def _product_plan(self, product: str, payload: ProductRequest):
        log_event(
            logger,
            "batchdata.plan.started",
            product=product,
            mode=self.api_mode,
            category_count=len(payload.selected_categories),
            location_count=len(payload.locations),
            rows_per_category=payload.rows_per_category,
            combination=payload.combination,
        )
        definitions = {
            "quick_lists": ("quickListsEnabled", "quickListUnitCost", None),
            "basic_property": ("basicPropertyEnabled", "basicPropertyUnitCost", None),
            "listing_data": ("listingEnabled", "listingUnitCost", LISTING_CATEGORIES),
            "pre_foreclosure": (
                "preForeclosureEnabled",
                "preForeclosureUnitCost",
                FORECLOSURE_CATEGORIES,
            ),
        }
        enabled_key, cost_key, allowed_categories = definitions[product]
        if not self.config[enabled_key]:
            raise UnsafeOperation(f"Enable {product.replace('_', ' ')} access first")
        categories = payload.selected_categories
        if allowed_categories is not None:
            categories = [key for key in categories if key in allowed_categories]
        if not categories:
            raise UnsafeOperation(
                "No selected category supports this BatchData product"
            )
        unit_cost = Decimal(str(self.config[cost_key]))
        calls = []
        for category in categories:
            request = {
                "searchCriteria": {
                    "quickList": category,
                    "query": ", ".join(payload.locations),
                },
                "options": {"take": payload.rows_per_category, "skip": 0},
            }
            estimated = unit_cost * payload.rows_per_category
            calls.append(
                {
                    "category": category,
                    "endpoint": "/api/v1/property/search",
                    "product": product,
                    "request": request,
                    "maximum_rows": payload.rows_per_category,
                    "estimated_cost": float(estimated),
                }
            )
        maximum = payload.rows_per_category * len(calls)
        calls = _location_calls(calls, payload.locations, payload.rows_per_category)
        total = unit_cost * maximum
        plan = {
            "mode": self.api_mode,
            "combination": payload.combination,
            "property_search_calls": len(calls),
            "maximum_skip_trace_calls": 0,
            "maximum_returned_rows": maximum,
            "calls": calls,
            "contact_enrichment_enabled": False,
            "skip_trace": None,
            "estimated_cost": float(total),
        }
        log_event(
            logger,
            "batchdata.plan.created",
            product=product,
            mode=self.api_mode,
            planned_provider_calls=len(calls),
            maximum_returned_rows=maximum,
            estimated_cost=float(total),
        )
        return plan

    def _preview_run(self, plan):
        return {
            "id": "preview",
            "status": "Preview",
            "configuration_version": self.config["configurationVersion"],
            "call_plan": plan,
            "estimated_cost": float(plan["estimated_cost"]),
            "actual_cost": 0.0,
            "returned_records": 0,
            "unique_properties": 0,
            "duplicate_properties": 0,
        }

    def _run_product(self, product: str, payload: ProductRequest):
        plan = self._product_plan(product, payload)
        if not payload.confirmed:
            log_event(
                logger,
                "batchdata.preview.ready",
                product=product,
                mode=self.api_mode,
                planned_provider_calls=len(plan["calls"]),
                maximum_returned_rows=plan["maximum_returned_rows"],
                estimated_cost=plan["estimated_cost"],
                provider_calls_executed=0,
                run_records_created=0,
                provider_records_created=0,
                archive_writes=0,
            )
            return self._preview_run(plan)
        if len(payload.reason.strip()) < 3:
            raise UnsafeOperation("Paid execution requires confirmation and a reason")
        run = Run(
            id=uuid.uuid4().hex,
            configuration_version=self.config["configurationVersion"],
            status="Approved",
            selected_categories=payload.selected_categories,
            call_plan=plan,
            estimated_cost=Decimal(str(plan["estimated_cost"])),
        )
        self.db.add(run)
        self.db.commit()
        log_event(
            logger,
            "batchdata.run.persisted",
            run_id=run.id,
            product=product,
            mode=self.api_mode,
            status=run.status,
            estimated_cost=float(run.estimated_cost),
        )
        return self.execute(run.id)

    def quick_lists(self, payload: ProductRequest):
        return self._run_product("quick_lists", payload)

    def basic_property(self, payload: ContactEnrichmentRequest):
        from .stages import selected_stage

        return selected_stage(self, "basic_property", payload)

    def listing_data(self, payload: ContactEnrichmentRequest):
        from .stages import selected_stage

        return selected_stage(self, "listing_data", payload)

    def pre_foreclosure(self, payload: ContactEnrichmentRequest):
        from .stages import selected_stage

        return selected_stage(self, "pre_foreclosure", payload)

    def contact_enrichment(self, payload: ContactEnrichmentRequest):
        from .stages import selected_stage

        return selected_stage(self, "contact_enrichment", payload)

    def create_plan(self):
        plan = self.call_plan()
        run = Run(
            id=uuid.uuid4().hex,
            configuration_version=self.config["configurationVersion"],
            status="Draft",
            selected_categories=self.config["selectedCategories"],
            call_plan=plan,
            estimated_cost=Decimal(str(plan["estimated_cost"])),
        )
        self.db.add(run)
        self.db.commit()
        return self.run_value(run)

    def transition(self, run_id, target):
        run = self.db.get(Run, run_id)
        if not run:
            raise UnsafeOperation("Run not found")
        allowed = {
            "Validated": {"Draft", "Requires Validation"},
            "Approved": {"Validated"},
        }
        if run.status not in allowed[target]:
            raise UnsafeOperation(f"A {run.status} run cannot be marked {target}")
        if run.configuration_version != self.config["configurationVersion"]:
            raise UnsafeOperation(
                "Configuration changed; create and validate a new call plan"
            )
        if target == "Validated":
            current = self.call_plan()
            if digest(current) != digest(run.call_plan):
                raise UnsafeOperation("Call plan no longer matches the configuration")
        run.status = target
        self.db.commit()
        return self.run_value(run)

    def _reserve(self, run):
        cfg = self.config
        estimate = Decimal(str(run.estimated_cost))
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        spent = Decimal(
            str(
                self.db.query(func.coalesce(func.sum(ApiCall.actual_cost), 0))
                .filter(ApiCall.created_at >= start)
                .scalar()
            )
        )
        reserved = Decimal(
            str(
                self.db.query(func.coalesce(func.sum(Reservation.amount), 0))
                .filter(Reservation.status.in_(["reserved", "reconciliation_required"]))
                .scalar()
            )
        )
        if spent + reserved + estimate > Decimal(str(cfg["monthlySpendCap"])):
            run.status = "Budget Blocked"
            self.db.commit()
            raise UnsafeOperation(
                "Estimated cost exceeds the monthly PAYG spending cap"
            )
        reservation = Reservation(run_id=run.id, amount=estimate)
        self.db.add(reservation)
        run.status = "Running"
        self.db.commit()
        log_event(
            logger,
            "batchdata.budget.reserved",
            run_id=run.id,
            estimate=float(estimate),
            spent=float(spent),
            already_reserved=float(reserved),
            monthly_cap=float(cfg["monthlySpendCap"]),
        )
        return reservation

    def execute(self, run_id):
        run = self.db.get(Run, run_id)
        if not run or run.status != "Approved":
            raise UnsafeOperation("Only an approved run can execute")
        if not self.row.enabled:
            raise UnsafeOperation("Enable BatchData before executing paid calls")
        if run.call_plan.get("mode", "live") != self.api_mode:
            raise UnsafeOperation("API mode changed; preview and approve a new run")
        sandbox = self.api_mode == "sandbox"
        log_event(
            logger,
            "batchdata.execution.started",
            run_id=run.id,
            mode=self.api_mode,
            planned_provider_calls=len(run.call_plan.get("calls", [])),
            estimated_cost=float(run.estimated_cost),
        )
        reservation = self._reserve(run)
        actual = Decimal(0)
        returned = 0
        unique = {}
        try:
            for call_index, planned in enumerate(run.call_plan["calls"]):
                product = planned.get("product")
                log_event(
                    logger,
                    "batchdata.provider_call.started",
                    run_id=run.id,
                    call_number=call_index + 1,
                    total_calls=len(run.call_plan["calls"]),
                    product=product,
                    endpoint=planned["endpoint"],
                    category=planned.get("category"),
                    location=planned.get("location"),
                    maximum_rows=planned.get("maximum_rows"),
                )
                if product in {
                    "quick_lists",
                    "basic_property",
                    "listing_data",
                    "pre_foreclosure",
                }:
                    response, request_id = getattr(self.client, product)(
                        planned["request"]
                    )
                else:
                    response, request_id = self.client.request(
                        "POST", planned["endpoint"], planned["request"]
                    )
                rows = _rows(response)
                log_event(
                    logger,
                    "batchdata.provider_call.completed",
                    run_id=run.id,
                    call_number=call_index + 1,
                    product=product,
                    request_id=request_id,
                    returned_records=len(rows),
                )
                unit = Decimal(str(planned["estimated_cost"])) / Decimal(
                    str(planned["maximum_rows"])
                )
                call_cost = Decimal(0) if sandbox else unit * len(rows)
                actual += call_cost
                returned += len(rows)
                call = ApiCall(
                    run_id=run.id,
                    endpoint=planned["endpoint"],
                    product=product or "property_search",
                    status="Completed",
                    request_json=_redact(planned["request"]),
                    response_json=_redact(response),
                    returned_records=len(rows),
                    estimated_cost=planned["estimated_cost"],
                    actual_cost=call_cost,
                    request_id=request_id,
                )
                self.db.add(call)
                archive_product = "/".join(
                    (
                        "quick-lists",
                        f"category={planned['category']}",
                        f"location={planned.get('location', 'combined')}",
                        f"call={call_index}",
                    )
                )
                self._save_file(run.id, archive_product, "request", planned["request"])
                self._save_file(run.id, archive_product, "response", response)
                for item in rows:
                    provider_id = item.get("_id") or item.get("id")
                    if isinstance(provider_id, int) and not isinstance(
                        provider_id, bool
                    ):
                        provider_id = str(provider_id)
                    if not isinstance(provider_id, str) or not provider_id:
                        raise UnsafeOperation(
                            "BatchData property result is missing _id or id"
                        )
                    unique.setdefault(provider_id, {"row": item, "categories": []})[
                        "categories"
                    ].append(planned["category"])
                self.db.commit()
                log_event(
                    logger,
                    "batchdata.api_call.persisted",
                    run_id=run.id,
                    api_call_id=call.id,
                    request_id=request_id,
                    returned_records=len(rows),
                    actual_cost=float(call_cost),
                )
            new_properties = []
            duplicate_count = returned - len(unique)
            log_event(
                logger,
                "batchdata.deduplication.completed",
                run_id=run.id,
                returned_records=returned,
                unique_provider_ids=len(unique),
                duplicate_records=duplicate_count,
                combination=run.call_plan.get("combination"),
            )
            if run.call_plan.get("combination") == "AND":
                required = set(run.selected_categories)
                unique = {
                    key: value
                    for key, value in unique.items()
                    if required.issubset(set(value["categories"]))
                }
            for provider_id, item in unique.items():
                prop = (
                    self.db.query(Property)
                    .filter_by(
                        provider=self.property_provider,
                        provider_property_id=provider_id,
                    )
                    .one_or_none()
                )
                if not prop:
                    row = item["row"]
                    address = (
                        row.get("address")
                        if isinstance(row.get("address"), dict)
                        else {}
                    )
                    prop = Property(
                        provider=self.property_provider,
                        provider_property_id=provider_id,
                        address_hash=address.get("hash"),
                        apn=row.get("apn") or row.get("APN"),
                        normalized_address=address.get("formatted")
                        or address.get("normalized")
                        or ", ".join(
                            str(address[key])
                            for key in ("street", "city", "state", "zip")
                            if address.get(key)
                        ),
                        immutable_provider_snapshot=_redact(row),
                        operational_copy={**_redact(row), "_quick_lists_run": run.id}
                        if product == "quick_lists"
                        else _redact(row),
                        skiptrace_status="waiting_for_skip_trace"
                        if self.config["contactEnrichmentEnabled"]
                        else "not_requested",
                    )
                    self.db.add(prop)
                    self.db.flush()
                    new_properties.append(prop)
                for category in dict.fromkeys(item["categories"]):
                    if product == "quick_lists" and not (
                        prop.operational_copy or {}
                    ).get("_quick_lists_run"):
                        prop.operational_copy = {
                            **(prop.operational_copy or {}),
                            "_quick_lists_run": run.id,
                        }
                    membership = (
                        self.db.query(Membership)
                        .filter_by(property_id=prop.id, category=category)
                        .one_or_none()
                    )
                    if membership:
                        membership.last_matched_at = utcnow()
                    else:
                        self.db.add(Membership(property_id=prop.id, category=category))
            self.db.commit()
            log_event(
                logger,
                "batchdata.properties.persisted",
                run_id=run.id,
                provider=self.property_provider,
                unique_properties=len(unique),
                new_properties=len(new_properties),
                existing_properties=len(unique) - len(new_properties),
            )
            run.status = "Completed"
            run.actual_cost = actual
            run.returned_records = returned
            run.unique_properties = len(unique)
            run.duplicate_properties = duplicate_count
            export_rows = [item["row"] for item in unique.values()]
            self.export_properties(run.id, export_rows)
            self.row.last_successful_sync_at = utcnow()
            self.row.last_successful_connection_at = utcnow()
            self.row.connection_status = "sandbox_verified" if sandbox else "connected"
            self.row.last_error = None
            reservation.amount = actual
            reservation.status = "reconciled"
            reservation.reconciled_at = utcnow()
            self.db.commit()
            log_event(
                logger,
                "batchdata.execution.completed",
                run_id=run.id,
                mode=self.api_mode,
                status=run.status,
                returned_records=returned,
                unique_properties=len(unique),
                duplicate_records=duplicate_count,
                actual_cost=float(actual),
                archive_backend=configured_storage().backend,
            )
            result = self.run_value(run)
            result["properties"] = [property_table_row(row) for row in export_rows]
            return result
        except Exception as error:
            self.db.rollback()
            run = self.db.get(Run, run_id)
            run.status = "Failed"
            run.error_message = str(error)[:2000]
            self.row.last_error = str(error)[:2000]
            reservation = self.db.query(Reservation).filter_by(run_id=run_id).one()
            reservation.status = "reconciliation_required"
            self.db.commit()
            log_event(
                logger,
                "batchdata.execution.failed",
                level=__import__("logging").ERROR,
                run_id=run_id,
                error_type=type(error).__name__,
                error=str(error),
                reconciliation_required=True,
            )
            raise

    def test_connection(self):
        if not os.getenv("BATCHDATA_API_TOKEN"):
            raise UnsafeOperation("BatchData API token is not configured")
        self.row.connection_status = "configured_unverified"
        self.row.last_connection_test_at = utcnow()
        self.row.last_error = None
        self.db.commit()
        return {
            "message": "Local credentials are configured; live verification occurs only during an approved execution"
        }

    def export_properties(self, run_id, rows):
        self._save_file(run_id, "properties", "properties", rows)
        table = [property_table_row(row) for row in rows]
        self._save_file(run_id, "properties", "properties_normalized", table)
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=list(property_table_row({})))
        writer.writeheader()
        for row in table:
            writer.writerow(
                {
                    key: "'" + value
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@"))
                    else value
                    for key, value in row.items()
                }
            )
        self._save_file(
            run_id, "properties", "properties_csv", output.getvalue(), extension="csv"
        )

    def _save_file(self, run_id, product, name, value, extension="json"):
        relative = run_archive_key(
            self.api_mode, run_id, product, name, extension
        )
        content = (
            value
            if extension == "csv"
            else json.dumps(_redact(value), indent=2, sort_keys=True, default=str)
        )
        encoded = content.encode("utf-8")
        storage = configured_storage()
        log_event(
            logger,
            "batchdata.archive.write.started",
            run_id=run_id,
            backend=storage.backend,
            kind=name,
            archive_key=relative,
            size_bytes=len(encoded),
        )
        storage.write(relative, encoded)
        log_event(
            logger,
            "batchdata.archive.write.completed",
            run_id=run_id,
            backend=storage.backend,
            kind=name,
            archive_key=relative,
            size_bytes=len(encoded),
        )
        self.db.add(
            SavedFile(
                run_id=run_id,
                kind=name,
                relative_path=relative,
                size_bytes=len(encoded),
            )
        )

    def run_value(self, run):
        value = {
            column.name: getattr(run, column.name) for column in run.__table__.columns
        }
        value["provider_calls"] = [
            {
                "request": call.request_json,
                "response": call.response_json,
                "request_id": call.request_id,
                "product": call.product,
            }
            for call in self.db.query(ApiCall)
            .filter_by(run_id=run.id)
            .order_by(ApiCall.id)
            .all()
        ]
        return value
