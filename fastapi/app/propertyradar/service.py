"""Durable, serialized provider operations with fail-closed purchase reservations."""

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from sqlalchemy import func, update

from .categories import CATEGORIES, criteria
from .client import Client, ProviderError
from .config import (
    PUBLIC_URL_MESSAGE,
    Configuration,
    ContactEnrichmentRequest,
    PropertyDetailsRequest,
    PropertySearchRequest,
    cycle,
    local_mode,
    public_https,
)
from .models import (
    Category,
    Email,
    IntegrationConfig,
    Membership,
    Phone,
    Preview,
    Property,
    ProviderList,
    SkiptraceJob,
    Snapshot,
    Usage,
    WebhookEvent,
    utcnow,
)


class UnsafeOperation(ValueError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def initialize(db):
    # API and worker startup can race; seeds must be safe across processes.
    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        from sqlalchemy.dialects.postgresql import insert
    db.execute(
        insert(IntegrationConfig)
        .values(
            provider="propertyradar",
            configuration_json=json.loads(Configuration().json()),
        )
        .on_conflict_do_nothing(index_elements=["provider"])
    )
    for key, item in CATEGORIES.items():
        db.execute(
            insert(Category)
            .values(key=key, label=item["label"], criteria_json=item["criteria"])
            .on_conflict_do_nothing(index_elements=["key"])
        )
    db.commit()
    return db.query(IntegrationConfig).filter_by(provider="propertyradar").one()


@contextmanager
def operation_lock(db):
    """Cross-process CAS mutex, committed separately so webhook inserts remain fast.

    Deliberately no lease expiry: an uncertain purchase must never be replayed by
    another worker. Crash recovery requires operator reconciliation (see docs).
    """
    token = uuid.uuid4().hex
    changed = db.execute(
        update(IntegrationConfig)
        .where(
            IntegrationConfig.provider == "propertyradar",
            IntegrationConfig.lock_token.is_(None),
        )
        .values(lock_token=token, lock_started_at=utcnow())
    ).rowcount
    db.commit()
    if changed != 1:
        raise UnsafeOperation(
            "Another integration operation is running. If its worker stopped, reconcile it before releasing the lock."
        )
    try:
        yield
    finally:
        db.rollback()
        db.execute(
            update(IntegrationConfig)
            .where(IntegrationConfig.lock_token == token)
            .values(lock_token=None, lock_started_at=None)
        )
        db.commit()


def results(data):
    rows = data.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise UnsafeOperation("Provider response is missing its results array")
    return rows


def first(data):
    rows = results(data)
    if not rows:
        raise UnsafeOperation("Provider returned no record")
    return rows[0]


def quote_values(data):
    try:
        count = int(data["resultCount"])
        free = int(data["quantityFreeRemaining"])
        cost = Decimal(str(data["totalCost"]))
        if count < 0 or free < 0 or not cost.is_finite() or cost < 0:
            raise ValueError()
        return count, free, cost
    except (KeyError, ValueError, TypeError, InvalidOperation):
        raise UnsafeOperation(
            "Provider preview is missing valid count, free allowance or cost; purchase blocked"
        ) from None


class Service:
    def __init__(self, db, client=None):
        self.db = db
        self.client = client or Client()

    @property
    def row(self):
        self.db.flush()
        return (
            self.db.query(IntegrationConfig)
            .filter_by(provider="propertyradar")
            .populate_existing()
            .one()
        )

    @property
    def config(self):
        row = self.row
        return {**row.configuration_json, "enabled": row.enabled}

    def enabled(self):
        if not self.row.enabled:
            raise UnsafeOperation("Local integration is paused")

    def billing_cycle(self):
        return cycle(self.config, utcnow().date())

    def call(
        self,
        method,
        path,
        params=None,
        body=None,
        usage_type="api_call",
        check_enabled=True,
        transport=None,
    ):
        if check_enabled:
            self.enabled()
        try:
            data, request_id = (
                transport(params or {}, body)
                if transport
                else self.client.request(method, path, params, body)
            )
        except ProviderError as error:
            self.db.add(
                Usage(
                    usage_type=usage_type,
                    endpoint=f"{method} {path}",
                    status="error",
                    error_message=str(error),
                )
            )
            self.row.last_error = str(error)
            self.db.commit()
            raise
        # Do not retain response bodies in API logs: webhook registration can return secrets.
        cost = data.get("totalCost")
        try:
            cost = Decimal(str(cost)) if cost is not None else None
            if cost is not None and not cost.is_finite():
                cost = None
        except InvalidOperation:
            cost = None
        try:
            billing = self.billing_cycle()[0]
        except ValueError:
            billing = None
        self.db.add(
            Usage(
                billing_cycle=billing,
                usage_type=usage_type,
                endpoint=f"{method} {path}",
                preview=(params or {}).get("Purchase") == 0,
                purchased=(params or {}).get("Purchase") == 1,
                result_count=data.get("resultCount", 0),
                quantity_free_remaining=data.get("quantityFreeRemaining"),
                total_cost=cost,
                request_id=request_id,
            )
        )
        self.db.commit()
        return data

    def used(self, kind):
        return int(
            self.db.query(func.coalesce(func.sum(Usage.result_count), 0))
            .filter(
                Usage.billing_cycle == self.billing_cycle()[0],
                Usage.usage_type == kind,
                Usage.operation_key.isnot(None),
            )
            .scalar()
        )

    def safe_quote(self, data, kind, expected=None):
        count, free, cost = quote_values(data)
        cap = self.config[
            {
                "property_export": "export_limit",
                "phone_unlock": "phone_limit",
                "email_unlock": "email_limit",
            }[kind]
        ]
        quantity = max(count, expected or 0)
        if (
            cost != 0
            or (kind in ("phone_unlock", "email_unlock") and free <= 0)
            or (quantity and free < quantity)
            or self.used(kind) + quantity > cap
        ):
            raise UnsafeOperation(
                f"{kind}: allowance or zero-cost safety check failed; narrow the request or wait for the next billing cycle"
            )
        return quantity

    def purchase(
        self,
        key,
        method,
        path,
        params,
        body,
        kind,
        radar_id=None,
        person_key=None,
        expected=None,
        transport=None,
    ):
        self.enabled()
        previous = self.db.query(Usage).filter_by(operation_key=key).first()
        if previous:
            if previous.status == "completed":
                return previous.response_payload
            raise UnsafeOperation(
                "Purchase outcome is unresolved; reconcile the reservation before retrying"
            )
        preview = self.call(
            method,
            path,
            {**params, "Purchase": 0},
            body,
            kind,
            transport=transport,
        )
        count = self.safe_quote(preview, kind, expected)
        self.enabled()
        reservation = Usage(
            billing_cycle=self.billing_cycle()[0],
            usage_type=kind,
            endpoint=f"{method} {path}",
            radar_id=radar_id,
            person_key=person_key,
            operation_key=key,
            result_count=count,
            total_cost=0,
            status="reserved",
        )
        self.db.add(reservation)
        self.db.commit()  # Durable BEFORE the external call; never release uncertain allowances.
        try:
            data = self.call(
                method,
                path,
                {**params, "Purchase": 1},
                body,
                kind,
                transport=transport,
            )
        except Exception:
            reservation.status = "uncertain"
            self.db.commit()
            raise
        reservation.status = "completed"
        reservation.purchased = True
        reservation.response_payload = data
        self.db.commit()
        # A provider-side billing race cannot be reversed. Stop all subsequent work.
        if Decimal(str(data.get("totalCost", "0"))) != 0:
            self.row.enabled = False
            self.row.last_error = "Provider reported a cost after a zero-cost preview. Processing paused; reconcile provider billing."
            self.db.commit()
            raise UnsafeOperation(self.row.last_error)
        return data

    def selected(self):
        cfg = self.config
        if not cfg["selected_categories"] or not any(
            cfg.get(f) for f in ("state", "city", "zip_codes", "county_fips")
        ):
            raise UnsafeOperation("Choose at least one category and geographic filter")
        return cfg["selected_categories"]

    def detail_params(self, ids):
        if not ids or len(ids) > 500:
            raise UnsafeOperation("Details batches must contain 1–500 RadarIDs")
        return {"Fields": "Overview,Persons", "Limit": len(ids), "Start": 0}, {
            "Criteria": [{"name": "RadarID", "value": ids}]
        }

    def search_properties(self, payload: PropertySearchRequest):
        cfg = {
            **self.config,
            "state": payload.state,
            "city": payload.city.strip(),
            "zip_codes": payload.zip_codes,
            "county_fips": payload.county_fips,
            "initial_rows": payload.rows_per_category,
            "selected_categories": payload.category_ids,
        }
        if not any(
            cfg.get(key) for key in ("state", "city", "zip_codes", "county_fips")
        ):
            raise UnsafeOperation("Choose at least one geographic filter")
        memberships: dict[str, list[str]] = {}
        raw: dict[str, list[str]] = {}
        for category_id in payload.category_ids:
            rows = results(
                self.call(
                    "POST",
                    "/v1/properties",
                    {
                        "Fields": "RadarID",
                        "Limit": payload.rows_per_category,
                        "Start": 0,
                        "Purchase": 0,
                    },
                    {"Criteria": criteria(category_id, cfg)},
                    "identification",
                    transport=self.client.search_properties,
                )
            )
            raw[category_id] = [str(row["RadarID"]) for row in rows]
            if len(raw[category_id]) > payload.rows_per_category:
                raise UnsafeOperation(
                    "Provider exceeded the requested identification limit"
                )
            for radar_id in raw[category_id]:
                memberships.setdefault(radar_id, [])
                if category_id not in memberships[radar_id]:
                    memberships[radar_id].append(category_id)
        total = sum(len(values) for values in raw.values())
        return {
            "raw_radar_ids": raw,
            "memberships": memberships,
            "total_before_deduplication": total,
            "unique_count": len(memberships),
            "duplicate_occurrences": total - len(memberships),
        }

    def fetch_property_details(self, payload: PropertyDetailsRequest):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Property details purchase requires confirmation and a reason"
            )
        existing = {
            row.radar_id: row
            for row in self.db.query(Property)
            .filter(Property.radar_id.in_(payload.radar_ids))
            .all()
        }
        created = 0
        for radar_id in payload.radar_ids:
            if radar_id in existing:
                for category_id in payload.category_ids:
                    self.membership(existing[radar_id], category_id, "manual_details")
                continue
            params, body = self.detail_params([radar_id])
            response = self.purchase(
                f"details:{radar_id}",
                "POST",
                "/v1/properties",
                params,
                body,
                "property_export",
                radar_id=radar_id,
                expected=1,
                transport=self.client.property_details,
            )
            rows = results(response)
            if len(rows) != 1 or str(rows[0].get("RadarID")) != radar_id:
                raise UnsafeOperation(
                    "Purchased response did not match the requested RadarID; reconcile the reservation"
                )
            prop = self.store_property(rows[0], "manual_details")
            for category_id in payload.category_ids:
                self.membership(prop, category_id, "manual_details")
            created += 1
            self.db.commit()
        self.row.last_successful_sync_at = utcnow()
        self.db.commit()
        return {
            "requested": len(payload.radar_ids),
            "created": created,
            "existing": len(existing),
        }

    def enrich_contacts(self, payload: ContactEnrichmentRequest):
        if not payload.confirmed or len(payload.reason.strip()) < 3:
            raise UnsafeOperation(
                "Contact enrichment requires confirmation and a reason"
            )
        properties = (
            self.db.query(Property)
            .filter(Property.radar_id.in_(payload.radar_ids))
            .all()
        )
        if len(properties) != len(payload.radar_ids):
            raise UnsafeOperation(
                "Contact enrichment accepts only saved, deduplicated PropertyRadar properties"
            )
        completed = 0
        already_completed = 0
        for prop in properties:
            job = self.db.query(SkiptraceJob).filter_by(property_id=prop.id).first()
            if job and job.status == "completed":
                already_completed += 1
                continue
            if not job:
                self.queue_skiptrace(prop)
                self.db.commit()
                job = self.db.query(SkiptraceJob).filter_by(property_id=prop.id).one()
            else:
                job.status = "pending"
                job.error_message = None
                self.db.commit()
            self.skiptrace(job)
            completed += 1
        return {
            "requested": len(payload.radar_ids),
            "completed": completed,
            "already_completed": already_completed,
        }

    def preview_import(self):
        cfg = self.config
        memberships, raw = {}, {}
        for key in self.selected():
            rows = results(
                self.call(
                    "POST",
                    "/v1/properties",
                    {
                        "Fields": "RadarID",
                        "Limit": cfg["initial_rows"],
                        "Start": 0,
                        "Purchase": 0,
                    },
                    {"Criteria": criteria(key, cfg)},
                    "identification",
                )
            )
            raw[key] = [str(r["RadarID"]) for r in rows]
            if len(raw[key]) > cfg["initial_rows"]:
                raise UnsafeOperation(
                    "Provider exceeded the requested identification limit"
                )
            for radar in raw[key]:
                memberships.setdefault(radar, [])
                if key not in memberships[radar]:
                    memberships[radar].append(key)
        existing = {
            p.radar_id
            for p in self.db.query(Property)
            .filter(Property.radar_id.in_(memberships))
            .all()
        }
        ids = sorted(set(memberships) - existing)
        previews, reason = [], None
        try:
            for offset in range(0, len(ids), 500):
                batch = ids[offset : offset + 500]
                params, body = self.detail_params(batch)
                value = self.call(
                    "POST",
                    "/v1/properties",
                    {**params, "Purchase": 0},
                    body,
                    "property_export",
                )
                previews.append(
                    {
                        k: value.get(k)
                        for k in ("resultCount", "totalCost", "quantityFreeRemaining")
                    }
                )
                self.safe_quote(value, "property_export", len(batch))
            if ids and self.used("property_export") + len(ids) > cfg["export_limit"]:
                raise UnsafeOperation("Combined import exceeds the local export limit")
            if previews and min(
                int(p["quantityFreeRemaining"]) for p in previews
            ) < len(ids):
                raise UnsafeOperation(
                    "Combined import exceeds the provider free allowance"
                )
        except ValueError as error:
            reason = str(error)
        total = sum(map(len, raw.values()))
        payload = {
            "selected_categories": list(raw),
            "rows_per_category": cfg["initial_rows"],
            "raw_radar_ids": raw,
            "total_before_deduplication": total,
            "unique_count": len(memberships),
            "duplicate_occurrences": total - len(memberships),
            "category_overlap": {r: c for r, c in memberships.items() if len(c) > 1},
            "memberships": memberships,
            "estimated_exports": len(ids),
            "existing_count": len(existing),
            "previews": previews,
            "safe": reason is None,
            "reason": reason,
        }
        preview = Preview(
            id=uuid.uuid4().hex, kind="import", config_hash=digest(cfg), payload=payload
        )
        self.db.add(preview)
        self.db.commit()
        return {"preview_id": preview.id, **payload}

    def get_preview(self, preview_id, kind):
        row = self.db.get(Preview, preview_id)
        if (
            not row
            or row.kind != kind
            or row.consumed
            or row.config_hash != digest(self.config)
        ):
            raise UnsafeOperation(
                "Preview is missing, consumed or configuration changed; preview again"
            )
        if row.created_at.replace(tzinfo=utcnow().tzinfo) < utcnow() - timedelta(
            minutes=15
        ):
            raise UnsafeOperation("Preview expired; preview again")
        if not row.payload["safe"]:
            raise UnsafeOperation(row.payload.get("reason") or "Preview is unsafe")
        return row

    def membership(self, prop, key, source, list_id=None):
        category = self.db.query(Category).filter_by(key=key).one()
        member = (
            self.db.query(Membership)
            .filter_by(property_id=prop.id, category_id=category.id)
            .first()
        )
        if not member:
            member = Membership(
                property_id=prop.id, category_id=category.id, match_source=source
            )
            self.db.add(member)
        member.last_matched_at = utcnow()
        member.is_active = True
        if list_id:
            member.provider_list_id = list_id
        self.db.flush()

    def snapshot(self, prop, payload, trigger):
        self.db.add(
            Snapshot(
                property_id=prop.id,
                snapshot_type=(
                    "details" if trigger in {"initial", "manual_details"} else "event"
                ),
                trigger_type=trigger,
                payload_hash=digest(payload),
                raw_payload=payload,
            )
        )

    def queue_skiptrace(self, prop):
        if not self.db.query(SkiptraceJob).filter_by(property_id=prop.id).first():
            self.db.add(
                SkiptraceJob(
                    property_id=prop.id,
                    radar_id=prop.radar_id,
                    selected_contact_mode=self.config["contact_mode"],
                )
            )
            prop.propertyradar_skiptrace_status = "pending"

    def store_property(self, payload, source):
        radar = str(payload["RadarID"])
        prop = self.db.query(Property).filter_by(radar_id=radar).first()
        if not prop:
            prop = Property(
                radar_id=radar,
                first_seen_source=source,
                current_provider_payload=payload,
                propertyradar_details_fetched_at=utcnow(),
            )
            self.db.add(prop)
            self.db.flush()
            self.snapshot(prop, payload, source)
            queue_setting = {
                "initial": "skiptrace_initial",
                "webhook": "skiptrace_new",
            }.get(source)
            if queue_setting and self.config[queue_setting]:
                self.queue_skiptrace(prop)
        return prop

    def import_initial(self, preview_id):
        preview = self.get_preview(preview_id, "import")
        memberships = preview.payload["memberships"]
        existing = {
            p.radar_id
            for p in self.db.query(Property)
            .filter(Property.radar_id.in_(memberships))
            .all()
        }
        ids = sorted(set(memberships) - existing)
        # One durable key per property prevents overlap across previews, batches and webhook jobs.
        for radar in ids:
            params, body = self.detail_params([radar])
            payload = self.purchase(
                f"details:{radar}",
                "POST",
                "/v1/properties",
                params,
                body,
                "property_export",
                radar_id=radar,
                expected=1,
            )
            rows = results(payload)
            if len(rows) != 1 or str(rows[0].get("RadarID")) != radar:
                raise UnsafeOperation(
                    "Purchased response did not match the requested RadarID; reconcile the reservation"
                )
            prop = self.store_property(rows[0], "initial")
            for key in memberships[radar]:
                self.membership(prop, key, "initial")
            self.db.commit()
        for radar, keys in memberships.items():
            prop = self.db.query(Property).filter_by(radar_id=radar).one()
            for key in keys:
                self.membership(prop, key, "initial")
        preview.consumed = True
        self.row.last_successful_sync_at = utcnow()
        self.db.commit()
        return {"created": len(ids), "unique_properties": len(memberships)}

    def prepare_lists(self):
        self.enabled()
        cfg = self.config
        for key in self.selected():
            cat = self.db.query(Category).filter_by(key=key).one()
            crit = criteria(key, cfg)
            if any(
                row.criteria_json == crit
                for row in self.db.query(ProviderList)
                .filter_by(category_id=cat.id)
                .all()
            ):
                continue
            name = f"ListingIQ - {cfg['city'] or cfg['state'] or 'Area'} - {cat.label}"[
                :50
            ]
            row = ProviderList(
                category_id=cat.id,
                list_name=name,
                criteria_json=crit,
                status="creating",
            )
            self.db.add(row)
            self.db.commit()  # Preserve ambiguous list creation; do not blindly repeat POST.
            try:
                data = first(
                    self.call(
                        "POST",
                        "/v1/lists",
                        body={
                            "ListName": name,
                            "ListType": "dynamic",
                            "isMonitored": 0,
                            "Criteria": crit,
                        },
                    )
                )
                row.provider_list_id = str(data["ListID"])
                row.total_count = int(data["TotalCount"])
                row.status = "prepared"
            except Exception:
                row.status = "error"
                row.error_message = (
                    "List creation needs reconciliation before another attempt"
                )
                self.db.commit()
                raise
            self.db.commit()
        return {"message": "Lists prepared without monitoring"}

    def list_rows(self):
        return self.db.query(ProviderList).order_by(ProviderList.id).all()

    def monitoring_candidates(self):
        cfg = self.config
        categories = {row.id: row.key for row in self.db.query(Category).all()}
        return [
            row
            for row in self.list_rows()
            if row.is_monitored
            or (
                categories[row.category_id] in cfg["selected_categories"]
                and row.criteria_json == criteria(categories[row.category_id], cfg)
            )
        ]

    def validate_monitoring(self):
        union, counts, reason = set(), {}, None
        rows = self.monitoring_candidates()
        if not rows:
            raise UnsafeOperation("Prepare lists first")
        try:
            for row in rows:
                if not row.provider_list_id:
                    raise UnsafeOperation(
                        "A list creation is unresolved; reconcile it first"
                    )
                path = f"/v1/lists/{quote(row.provider_list_id, safe='')}"
                data = first(self.call("GET", path, usage_type="monitoring_validation"))
                count = int(data["TotalCount"])
                if count < 0:
                    raise UnsafeOperation("Provider returned an invalid list count")
                row.total_count = count
                row.is_monitored = bool(int(data.get("isMonitored", 0)))
                row.last_synced_at = utcnow()
                automation = results(
                    self.call(
                        "GET", path + "/automations", usage_type="monitoring_validation"
                    )
                )
                row.automation_status = (
                    "enabled"
                    if automation and automation[0].get("isEnabled") in (1, True)
                    else "disabled"
                )
                if count > 10000:
                    raise UnsafeOperation(
                        f"{row.list_name} matches {count:,} properties; per-list limit is 10,000. Narrow geography or filters."
                    )
                ids = set()
                for start in range(0, 11000, 1000):
                    page = results(
                        self.call(
                            "GET",
                            path + "/items",
                            {"Fields": "RadarID", "Limit": 1000, "Start": start},
                            usage_type="monitoring_validation",
                        )
                    )
                    ids.update(str(r["RadarID"]) for r in page)
                    if len(page) < 1000:
                        break
                if len(ids) != count or len(ids) > 10000:
                    raise UnsafeOperation(
                        "List population changed or pagination was incomplete; validate again"
                    )
                union.update(ids)
                counts[row.provider_list_id] = len(ids)
                row.unique_count_at_validation = len(ids)
                row.status = "validated"
                row.error_message = None
                self.db.commit()
                if len(union) > self.config["monitored_limit"]:
                    raise UnsafeOperation(
                        "Full list union exceeds the monitored-property safety limit. Narrow geography or filters."
                    )
        except (ValueError, ProviderError) as error:
            reason = str(error)
            row.error_message = reason
            row.status = "error"
        payload = {
            "safe": reason is None,
            "reason": reason,
            "union_count": len(union),
            "counts": counts,
            "list_ids": [r.id for r in rows],
        }
        preview = Preview(
            id=uuid.uuid4().hex,
            kind="monitoring",
            config_hash=digest(self.config),
            payload=payload,
        )
        self.db.add(preview)
        self.db.commit()
        return {"preview_id": preview.id, **payload}

    def monitoring(self, enabled, preview_id=None):
        if enabled:
            prior = self.get_preview(preview_id, "monitoring")
            fresh = self.validate_monitoring()
            if not fresh["safe"]:
                raise UnsafeOperation(fresh["reason"])
            if (
                fresh["counts"] != prior.payload["counts"]
                or fresh["list_ids"] != prior.payload["list_ids"]
                or fresh["union_count"] != prior.payload["union_count"]
            ):
                raise UnsafeOperation(
                    "Monitoring population changed since confirmation; validate and confirm again"
                )
            prior.consumed = True
            self.db.commit()
        for row in self.monitoring_candidates() if enabled else self.list_rows():
            if not row.provider_list_id:
                continue
            self.call(
                "PATCH",
                f"/v1/lists/{quote(row.provider_list_id, safe='')}",
                body={"isMonitored": int(enabled)},
                check_enabled=enabled,
            )
            row.is_monitored = enabled
            row.status = "monitored" if enabled else "paused"
            row.monitoring_started_at = (
                utcnow() if enabled else row.monitoring_started_at
            )
            self.db.commit()
        return {
            "message": "Monitoring enabled"
            if enabled
            else "Monitoring paused; lists retained"
        }

    def pause_list(self, list_id):
        row = self.db.get(ProviderList, list_id)
        if not row or not row.provider_list_id:
            raise UnsafeOperation("Provider list is missing or unresolved")
        self.call(
            "PATCH",
            f"/v1/lists/{quote(row.provider_list_id, safe='')}",
            body={"isMonitored": 0},
            check_enabled=False,
        )
        row.is_monitored = False
        row.status = "paused"
        self.db.commit()
        return {"message": "List monitoring paused; list retained"}

    def register_webhook(self):
        self.enabled()
        url = (
            os.getenv("PROPERTYRADAR_PUBLIC_WEBHOOK_URL")
            or self.config["public_webhook_url"]
        )
        secret = os.getenv("PROPERTYRADAR_WEBHOOK_SECRET", "")
        if not public_https(url):
            raise UnsafeOperation(PUBLIC_URL_MESSAGE)
        if len(secret) < 32:
            raise UnsafeOperation(
                "Configure a random PROPERTYRADAR_WEBHOOK_SECRET of at least 32 characters on the server"
            )
        if not self.row.webhook_id:
            # Marker prevents duplicate registration after a network/process failure.
            self.row.webhook_id = "registration_pending"
            self.db.commit()
            data = first(
                self.call(
                    "POST",
                    "/v1/integrations/webhooks",
                    body={
                        "HookUrl": url,
                        "WebhookName": "ListingIQ Property Events",
                        "Secret": secret,
                    },
                )
            )
            self.row.webhook_id = str(data["WebhookID"])
            self.db.commit()
        if self.row.webhook_id == "registration_pending":
            raise UnsafeOperation(
                "Webhook registration is unresolved; reconcile its provider ID"
            )
        triggers = ",".join(
            t
            for flag, t in [
                ("monitor_new_matches", "New Matches"),
                ("monitor_status_changes", "Status Changes"),
            ]
            if self.config[flag]
        )
        for row in self.list_rows():
            if not row.provider_list_id:
                continue
            path = f"/v1/lists/{quote(row.provider_list_id, safe='')}/automations"
            existing = results(self.call("GET", path))
            merged = dict(existing[0]) if existing else {}
            merged.pop("Name", None)
            merged.pop("PurchasePhoneOptions", None)
            merged.pop("PurchaseEmailOptions", None)
            ids = set(str(merged.get("ExportToWebhookIDs") or "").split(",")) - {""}
            ids.add(self.row.webhook_id)
            merged.update(
                isEnabled=int(bool(triggers)), ExportToWebhookIDs=",".join(sorted(ids))
            )
            if triggers:
                merged["Triggers"] = triggers
            self.call("PUT", path, body=merged)
            row.automation_status = "enabled" if triggers else "disabled"
            self.db.commit()
        return {
            "message": "Webhook and list automations configured",
            "webhook_id": self.row.webhook_id,
        }

    def process_event(self, event):
        if event.is_test:
            event.processing_status = "test"
            event.processed_at = utcnow()
            self.db.commit()
            return
        self.enabled()
        done = (
            self.db.query(WebhookEvent)
            .filter(
                WebhookEvent.payload_hash == event.payload_hash,
                WebhookEvent.id != event.id,
                WebhookEvent.processing_status == "processed",
            )
            .first()
        )
        if done:
            event.is_retry_duplicate = True
            event.processing_status = "retry_duplicate"
            event.processed_at = utcnow()
            self.db.commit()
            return
        prop = self.db.query(Property).filter_by(radar_id=event.radar_id).first()
        if event.trigger_type == "New Match":
            event.is_duplicate_property = prop is not None
            if not prop:
                params, body = self.detail_params([event.radar_id])
                data = self.purchase(
                    f"details:{event.radar_id}",
                    "POST",
                    "/v1/properties",
                    params,
                    body,
                    "property_export",
                    radar_id=event.radar_id,
                    expected=1,
                )
                payload = first(data)
                if str(payload.get("RadarID")) != event.radar_id:
                    raise UnsafeOperation("Provider returned a different RadarID")
                prop = self.store_property(payload, "webhook")
            query = self.db.query(ProviderList)
            row = (
                query.filter_by(provider_list_id=event.provider_list_id).first()
                if event.provider_list_id
                else query.filter_by(list_name=event.list_name).first()
            )
            if row:
                category = self.db.get(Category, row.category_id)
                self.membership(prop, category.key, "webhook", row.provider_list_id)
        elif prop:
            self.snapshot(prop, event.raw_payload, event.trigger_type)
            current = dict(prop.current_provider_payload)
            history = list(current.get("_changes", []))
            for change in [event.change_1, event.change_2, event.change_3]:
                if change:
                    parts = change.split(":", 2)
                    if len(parts) == 3:
                        field, old, new = parts
                        current[field] = new
                        history.append(
                            {
                                "field": field,
                                "old": old,
                                "new": new,
                                "trigger": event.trigger_type,
                                "list": event.provider_list_id or event.list_name,
                                "at": event.received_at.isoformat(),
                            }
                        )
            current["_changes"] = history
            if event.trigger_type == "New Record":
                current["_last_new_record"] = event.raw_payload
            prop.current_provider_payload = current
        else:
            # Unknown Change/New Record remains in immutable delivery history; never buy it.
            event.processing_status = "unmatched"
        if event.processing_status != "unmatched":
            event.processing_status = "processed"
        event.processed_at = utcnow()
        self.row.last_successful_sync_at = utcnow()
        self.db.commit()

    def save_contacts(self, prop, people):
        for person in people:
            key = str(person.get("PersonKey", ""))
            for kind, model, field in [
                ("Phone", Phone, "normalized_phone"),
                ("Email", Email, "normalized_email"),
            ]:
                for contact in person.get(kind) or []:
                    if not isinstance(contact, dict):
                        continue
                    raw = str(contact.get("href") or contact.get("linktext") or "")
                    if kind == "Phone":
                        value = re.sub(r"\D", "", raw)
                        if len(value) == 10:
                            value = "1" + value
                        value = "+" + value if value else ""
                    else:
                        value = raw.removeprefix("mailto:").strip().lower()
                    if not value or (kind == "Email" and "@" not in value):
                        continue
                    if (
                        not self.db.query(model)
                        .filter_by(property_id=prop.id, **{field: value})
                        .first()
                    ):
                        item = model(
                            property_id=prop.id,
                            person_key=key,
                            **{field: value},
                            status=contact.get("status"),
                            source="propertyradar",
                        )
                        if kind == "Phone":
                            item.phone_type = contact.get("phoneType")
                        self.db.add(item)
                        self.db.flush()

    def skiptrace(self, job):
        self.enabled()
        prop = self.db.get(Property, job.property_id)
        if job.status == "completed":
            return
        job.started_at = utcnow()
        job.status = "processing"
        self.db.commit()
        people = prop.current_provider_payload.get("Persons")
        if not isinstance(people, list) or not people:
            path = f"/v1/properties/{quote(prop.radar_id, safe='')}/persons"
            data = self.call(
                "GET",
                path,
                {"Fields": "PersonKey,isPrimaryContact,OwnershipRole", "Purchase": 0},
                usage_type="owner_lookup",
                transport=lambda params, _body: self.client.property_persons(
                    quote(prop.radar_id, safe=""), params
                ),
            )
            # Identification must remain free. Fail closed if provider quotes a cost.
            _, _, cost = quote_values(data)
            if cost != 0:
                raise UnsafeOperation(
                    "Owner identification has a cost; automatic skip tracing blocked"
                )
            people = results(data)
        people = [
            p
            for p in people
            if p.get("PersonKey")
            and p.get("OwnershipRole") in ("Owner", "Principal", "Trustee")
        ]
        if job.selected_contact_mode == "primary":
            people = [p for p in people if p.get("isPrimaryContact") in (1, True)][:1]
        if not people:
            raise UnsafeOperation(
                "No supported owner/primary contact found; review provider data"
            )
        for person in people:
            key = str(person["PersonKey"])
            for kind, usage_kind in [
                ("Phone", "phone_unlock"),
                ("Email", "email_unlock"),
            ]:
                self.purchase(
                    f"unlock:{key}:{kind}",
                    "POST",
                    f"/v1/persons/{quote(key, safe='')}/{kind}",
                    {},
                    None,
                    usage_kind,
                    radar_id=prop.radar_id,
                    person_key=key,
                    transport=lambda params, _body, person_key=key, contact_kind=kind: (
                        self.client.unlock_person_contact(
                            quote(person_key, safe=""), contact_kind, params
                        )
                    ),
                )
            # Unlock endpoints return counts, not contacts. Read unlocked values via property persons.
            path = f"/v1/properties/{quote(prop.radar_id, safe='')}/persons"
            data = self.call(
                "GET",
                path,
                {"Fields": "PersonKey,Phone,Email", "Purchase": 0},
                usage_type="contact_read",
                transport=lambda params, _body: self.client.property_persons(
                    quote(prop.radar_id, safe=""), params
                ),
            )
            _, _, cost = quote_values(data)
            if cost != 0:
                raise UnsafeOperation(
                    "Reading unlocked contacts has a cost; review provider account"
                )
            contacts = [p for p in results(data) if str(p.get("PersonKey")) == key]
            self.save_contacts(prop, contacts)
            self.snapshot(prop, {"Persons": contacts}, "skiptrace")
            self.db.commit()
        prop.propertyradar_skiptrace_status = "completed"
        job.status = "completed"
        job.completed_at = utcnow()
        job.error_message = None
        self.db.commit()

    def work_once(self):
        if not self.row.enabled:
            return False
        # Dynamic populations can grow. Revalidate periodically while processing is enabled.
        active = self.db.query(ProviderList).filter_by(is_monitored=True).first()
        latest = (
            self.db.query(Preview)
            .filter_by(kind="monitoring")
            .order_by(Preview.created_at.desc())
            .first()
        )
        if active and (
            not latest
            or latest.created_at.replace(tzinfo=utcnow().tzinfo)
            < utcnow() - timedelta(minutes=5)
        ):
            checked = self.validate_monitoring()
            if not checked["safe"]:
                self.monitoring(False)
                self.row.last_error = checked["reason"]
                self.db.commit()
            return True
        event = (
            self.db.query(WebhookEvent)
            .filter_by(processing_status="pending")
            .order_by(WebhookEvent.id)
            .first()
        )
        if event:
            try:
                self.process_event(event)
            except (ValueError, ProviderError) as error:
                event.processing_status = (
                    "budget_deferred" if isinstance(error, UnsafeOperation) else "error"
                )
                event.error_message = str(error)
                self.db.commit()
            return True
        job = (
            self.db.query(SkiptraceJob)
            .filter(SkiptraceJob.status.in_(["pending", "processing"]))
            .order_by(SkiptraceJob.id)
            .first()
        )
        if job:
            try:
                self.skiptrace(job)
            except (ValueError, ProviderError) as error:
                job.status = (
                    "budget_deferred" if isinstance(error, UnsafeOperation) else "error"
                )
                job.error_message = str(error)
                try:
                    job.deferred_until = (
                        __import__("datetime")
                        .datetime.fromisoformat(self.billing_cycle()[1])
                        .replace(tzinfo=utcnow().tzinfo)
                    )
                except ValueError:
                    pass
                self.db.get(
                    Property, job.property_id
                ).propertyradar_skiptrace_status = job.status
                self.db.commit()
            return True
        return False

    def summary(self):
        cfg = self.config
        row = self.row
        try:
            start, end = self.billing_cycle()
            used = {
                kind: self.used(kind)
                for kind in ("property_export", "phone_unlock", "email_unlock")
            }
        except ValueError:
            start, end, used = (
                None,
                None,
                {
                    kind: 0
                    for kind in ("property_export", "phone_unlock", "email_unlock")
                },
            )
        token = bool(os.getenv("PROPERTYRADAR_API_TOKEN"))
        status = "Not configured" if not token else "Configured"
        if row.last_successful_connection_at:
            status = "Connected"
        if row.enabled and token:
            status = "Active"
        elif row.last_successful_connection_at:
            status = "Paused"
        if row.last_error:
            status = "Error"
        # A missing token isn't a runtime error to alarm over — it's just an
        # unfinished setup step, so it always wins over a stale last_error.
        if not token:
            status = "Not configured"
        webhook_url = (
            os.getenv("PROPERTYRADAR_PUBLIC_WEBHOOK_URL") or cfg["public_webhook_url"]
        )
        last_event = (
            self.db.query(WebhookEvent).order_by(WebhookEvent.id.desc()).first()
        )
        validation = (
            self.db.query(Preview)
            .filter_by(kind="monitoring")
            .order_by(Preview.created_at.desc())
            .first()
        )
        now = utcnow()
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)
        metrics = {
            "unique_properties": self.db.query(Property).count(),
            "initial_properties": self.db.query(Property)
            .filter_by(first_seen_source="initial")
            .count(),
            "new_webhook_properties": self.db.query(Property)
            .filter_by(first_seen_source="webhook")
            .count(),
            "duplicate_new_matches": self.db.query(WebhookEvent)
            .filter_by(is_duplicate_property=True)
            .count(),
            "active_monitored_lists": self.db.query(ProviderList)
            .filter_by(is_monitored=True)
            .count(),
            "unique_monitored_properties_at_validation": validation.payload[
                "union_count"
            ]
            if validation
            else None,
            "webhook_events": self.db.query(WebhookEvent).count(),
            "pending_skiptrace_jobs": self.db.query(SkiptraceJob)
            .filter(SkiptraceJob.status != "completed")
            .count(),
            "deferred_records": self.db.query(SkiptraceJob)
            .filter_by(status="budget_deferred")
            .count()
            + self.db.query(WebhookEvent)
            .filter_by(processing_status="budget_deferred")
            .count(),
        }
        # Time-windowed lead activity: how many leads are new, and how many
        # existing leads received a provider-reported change, in the last
        # week/month. Distinct from `metrics` above, which are all-time totals.
        lead_activity = {
            "new_leads_7d": self.db.query(Property)
            .filter(Property.first_seen_at >= week_ago)
            .count(),
            "new_leads_30d": self.db.query(Property)
            .filter(Property.first_seen_at >= month_ago)
            .count(),
            "lead_updates_7d": self.db.query(WebhookEvent)
            .filter(
                WebhookEvent.trigger_type == "Change",
                WebhookEvent.received_at >= week_ago,
            )
            .count(),
            "lead_updates_30d": self.db.query(WebhookEvent)
            .filter(
                WebhookEvent.trigger_type == "Change",
                WebhookEvent.received_at >= month_ago,
            )
            .count(),
        }
        # One row per prepared category = one "job" whose result is a list of
        # matched leads (ProviderList.total_count). This is the same table the
        # Lead Lists tab renders, summarized per category for the overview.
        jobs = self.db.query(ProviderList).order_by(ProviderList.id).all()
        job_categories = {row.id: row.label for row in self.db.query(Category).all()}
        jobs_by_category = [
            {
                "category": job_categories.get(row.category_id, "—"),
                "list_name": row.list_name,
                "total_count": row.total_count,
                "is_monitored": row.is_monitored,
                "automation_status": row.automation_status,
                "last_synced_at": row.last_synced_at,
            }
            for row in jobs
        ]
        return {
            "provider": "propertyradar",
            "status": status,
            "enabled": row.enabled,
            "config": {**cfg, "enabled": row.enabled},
            "token_configured": token,
            "connection_status": row.connection_status,
            "last_successful_connection_at": row.last_successful_connection_at,
            "last_successful_sync_at": row.last_successful_sync_at,
            "last_webhook_at": last_event.received_at if last_event else None,
            "last_error": row.last_error,
            "busy": bool(row.lock_token),
            "local_mode": local_mode(),
            "webhook_url": webhook_url,
            "webhook_id": row.webhook_id,
            "webhook_secret_configured": len(
                os.getenv("PROPERTYRADAR_WEBHOOK_SECRET", "")
            )
            >= 32,
            "webhook_registration_allowed": public_https(webhook_url),
            "webhook_message": None
            if public_https(webhook_url)
            else PUBLIC_URL_MESSAGE,
            "categories": list(CATEGORIES.values()),
            "metrics": metrics,
            "lead_activity": lead_activity,
            "jobs_by_category": jobs_by_category,
            "usage": {
                "cycle_start": start,
                "cycle_end": end,
                "used": used,
                "remaining": {
                    kind: max(0, cfg[limit] - used[kind])
                    for kind, limit in [
                        ("property_export", "export_limit"),
                        ("phone_unlock", "phone_limit"),
                        ("email_unlock", "email_limit"),
                    ]
                },
                "provider_reported_cost": str(
                    self.db.query(func.coalesce(func.sum(Usage.total_cost), 0))
                    .filter(
                        Usage.billing_cycle == start,
                        Usage.preview.is_(False),
                        Usage.operation_key.is_(None),
                    )
                    .scalar()
                ),
                "no_overage": not self.db.query(Usage)
                .filter(Usage.preview.is_(False), Usage.total_cost > 0)
                .first(),
            },
            "max_initial_rows": 100,
            "per_list_limit": 10000,
        }
