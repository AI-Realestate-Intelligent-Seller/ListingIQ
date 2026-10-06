"""Local provider data preparation and explicit, transactional distribution."""

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import update

from ..batchdata import models as bd
from ..batchdata.service import property_table_row
from ..dealmachine import models as dm
from ..leads.address import canonical
from ..leads.importer import normalize_phone, score_lead
from ..models import Lead, User
from ..propertyradar import models as pr
from .models import CombinedProperty, DistributionRun

PROVIDERS = {
    "batchdata": "BatchData",
    "propertyradar": "PropertyRadar",
    "dealmachine": "DealMachine",
}


class DataError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode()
    ).hexdigest()


def property_key(address):
    # City/state and unit remain significant; ZIP formatting is not an identity.
    return re.sub(r"\s+\d{5}(?:\s+\d{4})?$", "", canonical(address)).strip()


def object_value(value):
    return value if isinstance(value, dict) else {}


def phone_entry(value, blocked=False, owner_name=None):
    raw = object_value(value)
    number = raw.get("number") or raw.get("phone") if raw else value
    number = normalize_phone(str(number or ""))
    if not number:
        return None
    restricted = blocked or any(
        raw.get(key) in (True, "true", "Y", "yes")
        for key in ("dnc", "doNotCall", "do_not_call")
    )
    entry = {**raw, "number": number, "dnc": restricted}
    if owner_name:
        entry["owner_name"] = owner_name
    return entry


def _person_name(person, property_owners):
    """Prefer the name used on the deed when it matches a contact alias."""
    name = object_value(person.get("name"))
    choices = [name.get("full")]
    choices.extend(
        object_value(alias).get("full") for alias in name.get("akas", []) or []
    )
    normalized = {
        re.sub(r"[^a-z0-9]", "", str(choice).lower()): str(choice)
        for choice in choices
        if choice
    }
    for owner in property_owners:
        owner_name = object_value(object_value(owner).get("name")).get("full")
        key = re.sub(r"[^a-z0-9]", "", str(owner_name or "").lower())
        if key and key in normalized:
            return str(owner_name)
    return str(name.get("full") or "")


def normalized_record(provider, row, raw, categories, phones, sandbox):
    raw = object_value(raw)
    address = object_value(raw.get("address"))
    street = address.get("street")
    if not street and isinstance(raw.get("address"), str):
        street = raw["address"]
    street = (
        street
        or raw.get("street")
        or raw.get("address_line_1")
        or raw.get("Address")
        or raw.get("SiteAddress")
    )
    city = address.get("city") or raw.get("city") or raw.get("City")
    state = address.get("state") or raw.get("state") or raw.get("State")
    postal = (
        address.get("zip") or raw.get("zip") or raw.get("zip_code") or raw.get("Zip")
    )
    full = (
        raw.get("full_address")
        or raw.get("property_address")
        or address.get("formatted")
    )
    if street and city and state:
        full = ", ".join(str(item) for item in (street, city, state, postal) if item)
    elif not full:
        full = str(street or "")
    owner = object_value(raw.get("owner"))
    building, valuation, listing = (
        object_value(raw.get(key)) for key in ("building", "valuation", "listing")
    )
    owner_name = (
        owner.get("fullName")
        or raw.get("owner_name")
        or raw.get("OwnerName")
        or raw.get("Owner1Name")
    )
    full = full if isinstance(full, str) else ""
    key = property_key(full)
    # Preserve an incomplete property for review without merging it with others.
    valid = bool(
        full
        and re.match(r"\s*\d+\S*\s+\S", full)
        and (city and state or re.search(r",\s*[^,\d]+(?:,|\s)\s*[A-Za-z]{2}\b", full))
    )
    source_id = getattr(row, "provider_property_id", None) or getattr(
        row, "radar_id", None
    )
    normalized_phones = {}
    for entry in phones:
        if entry:
            previous = normalized_phones.get(entry["number"])
            merged = {**(previous or {}), **entry}
            merged["dnc"] = entry["dnc"] or bool(previous and previous["dnc"])
            normalized_phones[entry["number"]] = merged
    phone_list = list(normalized_phones.values())
    allowed = [item for item in phone_list if not item["dnc"]]
    status = (
        "ready"
        if allowed and valid
        else "dnc"
        if phone_list and not allowed
        else "needs_review"
    )
    return {
        "provider": provider,
        "source_id": str(source_id),
        "source_record_id": row.id,
        "mode": "sandbox" if sandbox else "live",
        "property_key": key if valid else f"source:{provider}:{source_id}",
        "categories": sorted(set(categories)),
        "lead_status": status,
        "data": {
            "address": str(full or ""),
            "address_valid": valid,
            "owner": str(owner_name or ""),
            "city": str(city or ""),
            "state": str(state or ""),
            "phones": phone_list,
            "beds": building.get("bedroomCount") or raw.get("beds"),
            "baths": building.get("calculatedBathroomCount") or raw.get("baths"),
            "estimated_value": valuation.get("estimatedValue")
            or raw.get("estimated_value"),
            "listing_status": listing.get("status") or raw.get("listing_status"),
            "listing_price": listing.get("price") or raw.get("listing_price"),
            "property_type": object_value(raw.get("general")).get("propertyTypeDetail")
            or raw.get("property_type"),
            "area_sqft": building.get("livingAreaSquareFeet")
            or building.get("totalBuildingAreaSquareFeet")
            or raw.get("area_sqft"),
            "equity_percent": valuation.get("equityPercent")
            or raw.get("equity_percent"),
        },
    }


def lead_details_payload(data, sources, run_id):
    return {
        **data,
        "_phone_numbers": [
            {**phone, "phone": phone["number"]} for phone in data.get("phones", [])
        ],
        "provider_sources": sources,
        "distribution_run_id": run_id,
    }


def _details_dict(lead):
    try:
        parsed = json.loads(lead.details or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _has_contact_mapping(lead):
    details = _details_dict(lead)
    phones = details.get("_phone_numbers") or details.get("phones") or []
    return (
        bool(details.get("provider_property_details"))
        and bool(details.get("contacts"))
        and bool(phones)
        and all(isinstance(phone, dict) and phone.get("owner_name") for phone in phones)
    )


def _sync_distributed_lead(lead, row, data, sources):
    """Backfill a pool lead or generated draft contact from combined evidence."""
    old_details = _details_dict(lead)
    operational = {
        key: old_details[key]
        for key in ("_campaign_original", "_parent_lead_id")
        if key in old_details
    }
    phone_map = {phone["number"]: phone for phone in data.get("phones", [])}
    normalized = normalize_phone(lead.phone or "")
    matched = phone_map.get(normalized)
    payload = lead_details_payload(data, sources, row.distribution_run_id)
    payload.update(operational)
    if lead.source == "provider_distribution_contact" and matched:
        payload["_phone_numbers"] = [{**matched, "phone": matched["number"]}]
    lead.details = json.dumps(payload)
    if matched and matched.get("owner_name"):
        lead.owner_name = matched["owner_name"]
    if lead.source == "provider_distribution" and lead.campaign_id is None:
        allowed = [phone for phone in data.get("phones", []) if not phone["dnc"]]
        lead.owner_name = data.get("owner")
        lead.phone = allowed[0]["number"] if allowed else None
        lead.dnc = bool(data.get("phones") and not allowed)
    lead.refreshed_at = datetime.utcnow()


def backfill_distributed_leads(db, leads):
    """Repair legacy distributed rows from saved provider data, including drafts."""
    targets = [
        lead for lead in leads
        if lead.source in {"provider_distribution", "provider_distribution_contact"}
        and lead.conversation_id is None
        and not _has_contact_mapping(lead)
    ]
    if not targets:
        return False

    # Rebuild current combined rows first. This retains immutable provider
    # snapshots and adds the richer contact/property fields introduced later.
    combine(db, "live")
    repaired = False
    for lead in targets:
        details = _details_dict(lead)
        parent_id = details.get("_parent_lead_id")
        master_id = int(parent_id) if parent_id else lead.id
        row = db.query(CombinedProperty).filter_by(lead_id=master_id, mode="live").first()
        if not row:
            continue
        _sync_distributed_lead(lead, row, row.data_json, row.sources_json)
        repaired = True
    db.flush()
    return repaired


def source_records(db, provider, mode):
    records = []
    categories = defaultdict(list)
    if provider == "batchdata":
        for membership in db.query(bd.Membership).all():
            categories[membership.property_id].append(membership.category)
        for row in db.query(bd.Property).order_by(bd.Property.id).all():
            sandbox = row.provider == "batchdata_sandbox"
            if sandbox != (mode == "sandbox"):
                continue
            operational = row.operational_copy or {}
            if not (
                operational.get("_property_search_run")
                or operational.get("_quick_lists_run")
            ):
                continue
            raw = dict(row.immutable_provider_snapshot)
            stages = operational.get("_stages", {})
            details = object_value(stages.get("details", {}).get("data"))
            raw.update(
                {
                    key: value
                    for key, value in details.items()
                    if key not in ("address", "_id", "id")
                }
            )
            phones = []
            contacts = []
            contact_match_metadata = []
            for result in stages.get("contacts", {}).get("data", []) or []:
                contact_match_metadata.append(
                    {key: value for key, value in result.items() if key != "persons"}
                )
                property_owners = (
                    object_value(result.get("property")).get("owners", []) or []
                )
                for person in result.get("persons", []) or []:
                    owner_name = _person_name(person, property_owners)
                    person_phones = [
                        phone_entry(phone, owner_name=owner_name)
                        for phone in person.get("phones", []) or []
                    ]
                    person_phones = [phone for phone in person_phones if phone]
                    phones.extend(person_phones)
                    contacts.append({**person, "owner_name": owner_name,
                                     "phones": person_phones})
            record = normalized_record(
                provider, row, raw, categories[row.id], phones, sandbox
            )
            # Keep the complete saved-property/detail payload available to the
            # brokerage drawer. Normalized fields drive filtering and scoring;
            # this copy supplies the full provider evidence behind them.
            record["data"]["provider_property_details"] = raw
            if contacts:
                record["data"]["contacts"] = contacts
            if contact_match_metadata:
                record["data"]["contact_match_metadata"] = contact_match_metadata
            record["table_data"] = property_table_row(row.immutable_provider_snapshot)
            records.append(record)
    elif provider == "propertyradar":
        for membership, category in (
            db.query(pr.Membership, pr.Category)
            .join(pr.Category)
            .filter(pr.Membership.is_active.is_(True))
            .all()
        ):
            categories[membership.property_id].append(category.key)
        phone_map = defaultdict(list)
        for phone in db.query(pr.Phone).all():
            phone_map[phone.property_id].append(
                phone_entry(
                    phone.normalized_phone,
                    str(phone.status).lower() in {"dnc", "opted_out", "do_not_call"},
                )
            )
        for row in db.query(pr.Property).order_by(pr.Property.id).all():
            sandbox = row.radar_id.startswith("TEST-")
            if sandbox == (mode == "sandbox"):
                records.append(
                    normalized_record(
                        provider,
                        row,
                        row.current_provider_payload,
                        categories[row.id],
                        phone_map[row.id],
                        sandbox,
                    )
                )
    elif provider == "dealmachine":
        for membership, category in (
            db.query(dm.Membership, dm.Category)
            .join(dm.Category)
            .filter(dm.Membership.active.is_(True))
            .all()
        ):
            categories[membership.property_id].append(category.key)
        phone_map = defaultdict(list)
        for link, point in (
            db.query(dm.PropertyContact, dm.ContactPoint)
            .join(
                dm.ContactPoint,
                dm.ContactPoint.person_id == dm.PropertyContact.person_id,
            )
            .filter(dm.ContactPoint.kind == "phone")
            .all()
        ):
            phone_map[link.property_id].append(
                phone_entry(
                    {**(point.metadata_json or {}), "number": point.display_value},
                    point.do_not_call is True,
                )
            )
        for row in db.query(dm.Property).order_by(dm.Property.id).all():
            sandbox = (
                row.provider.endswith("_sandbox")
                or (row.operational_json or {}).get("_test") is True
            )
            if sandbox == (mode == "sandbox"):
                records.append(
                    normalized_record(
                        provider,
                        row,
                        row.operational_json,
                        categories[row.id],
                        phone_map[row.id],
                        sandbox,
                    )
                )
    else:
        raise DataError("Unknown provider")
    return records


def combine(db, mode):
    groups = defaultdict(list)
    total = 0
    for provider in PROVIDERS:
        for record in source_records(db, provider, mode):
            groups[record["property_key"]].append(record)
            total += 1
    blocked_numbers = (
        {
            normalize_phone(lead.phone)
            for lead in db.query(Lead).filter(Lead.dnc.is_(True)).all()
            if lead.phone
        }
        if mode == "live"
        else set()
    )
    created = changed = 0
    existing = {
        row.property_key: row
        for row in db.query(CombinedProperty).filter_by(mode=mode).all()
    }
    for key, records in groups.items():
        data = {}
        cats, sources, phones = set(), [], {}
        for record in records:
            for field, value in record["data"].items():
                if (
                    field != "phones"
                    and value is not None
                    and value != ""
                    and value is not False
                ):
                    data.setdefault(field, value)
            for phone in record["data"]["phones"]:
                previous = phones.get(phone["number"])
                merged = {**(previous or {}), **phone}
                merged["dnc"] = (
                    phone["dnc"]
                    or phone["number"] in blocked_numbers
                    or bool(previous and previous["dnc"])
                )
                phones[phone["number"]] = merged
            cats.update(record["categories"])
            sources.append(
                {
                    "provider": record["provider"],
                    "source_id": record["source_id"],
                    "source_record_id": record["source_record_id"],
                }
            )
        data["phones"] = sorted(phones.values(), key=lambda item: item["number"])
        allowed = [phone for phone in phones.values() if not phone["dnc"]]
        status = (
            "ready"
            if allowed and data.get("address_valid")
            else "dnc"
            if phones and not allowed
            else "needs_review"
        )
        content_hash = digest(
            {
                "data": data,
                "sources": sources,
                "categories": sorted(cats),
                "status": status,
            }
        )
        row = existing.get(key)
        if row is None:
            row = CombinedProperty(mode=mode, property_key=key)
            db.add(row)
            created += 1
        elif row.content_hash != content_hash:
            changed += 1
        if row.content_hash != content_hash:
            row.data_json, row.sources_json, row.categories_json = (
                data,
                sources,
                sorted(cats),
            )
            row.lead_status, row.content_hash = status, content_hash
        # A later provider refresh must enrich the untouched lead already in a
        # brokerage pool; distribution is intentionally one-time inventory.
        if mode == "live" and row.lead_id:
            lead = db.get(Lead, row.lead_id)
            if (lead and lead.source == "provider_distribution"
                    and lead.campaign_id is None and lead.conversation_id is None):
                _sync_distributed_lead(lead, row, data, sources)
    db.flush()
    return {
        "source_records": total,
        "unique_properties": len(groups),
        "duplicates_removed": total - len(groups),
        "created": created,
        "updated": changed,
    }


def brokerage_rows(db):
    grouped = {}
    users = (
        db.query(User)
        .filter(
            User.brokerage_id.isnot(None),
            User.is_active.is_(True),
            User.role.in_(["hob", "broker"]),
        )
        .order_by(User.id)
        .all()
    )
    for user in sorted(users, key=lambda item: (item.role != "hob", item.id)):
        grouped.setdefault(
            user.brokerage_id,
            {
                "id": user.brokerage_id,
                "name": user.brokerage_name or user.brokerage_id,
                "recipient_id": user.id,
                "recipient_name": user.full_name or user.email,
            },
        )
    return list(grouped.values())


def preview_distribution(db, payload):
    organizations = {row["id"]: row for row in brokerage_rows(db)}
    rows = (
        db.query(CombinedProperty)
        .filter_by(mode=payload.mode, brokerage_id=None)
        .order_by(CombinedProperty.id)
        .all()
    )
    # Existing tenant pools already own these properties. Do not create a second
    # prospect in another brokerage or repeat an import in its current pool.
    existing_keys = (
        {
            property_key(lead.property_address)
            for lead in db.query(Lead).all()
            if lead.property_address
        }
        if payload.mode == "live"
        else set()
    )
    available = [
        row
        for row in rows
        if row.data_json.get("address_valid") and row.property_key not in existing_keys
    ]
    allocations, summaries = [], []
    for index, rule in enumerate(payload.rules):
        org = organizations.get(rule.brokerage_id)
        if not org:
            raise DataError("Choose a brokerage with an active head or broker")
        matches = [
            row
            for row in available
            if (
                not rule.categories
                or set(rule.categories).intersection(row.categories_json)
            )
            and (not rule.lead_statuses or row.lead_status in rule.lead_statuses)
        ]
        chosen = matches[: rule.quantity]
        ids = {row.id for row in chosen}
        available = [row for row in available if row.id not in ids]
        allocations.extend(
            {
                "property_id": row.id,
                "address": row.data_json.get("address"),
                "categories": row.categories_json,
                "lead_status": row.lead_status,
                "content_hash": row.content_hash,
                "brokerage_id": org["id"],
                "brokerage_name": org["name"],
                "recipient_id": org["recipient_id"],
                "rule_index": index,
            }
            for row in chosen
        )
        summaries.append(
            {
                "brokerage_id": org["id"],
                "brokerage_name": org["name"],
                "requested": rule.quantity,
                "allocated": len(chosen),
                "shortfall": rule.quantity - len(chosen),
            }
        )
    plan = {
        "mode": payload.mode,
        "rules": [rule.dict() for rule in payload.rules],
        "allocations": allocations,
        "summaries": summaries,
        "allocated": len(allocations),
        "remaining": len(available),
        "excluded": len(rows) - len(available) - len(allocations),
    }
    plan["preview_hash"] = digest(plan)
    return plan


def distribute(db, payload, actor_id):
    if not payload.confirmed:
        raise DataError("Distribution requires explicit confirmation")
    previous = (
        db.query(DistributionRun)
        .filter_by(preview_hash=payload.preview_hash)
        .one_or_none()
    )
    if previous:
        if (
            previous.mode != payload.mode
            or previous.actor_id != actor_id
            or previous.plan_json["rules"] != [rule.dict() for rule in payload.rules]
        ):
            raise DataError(
                "This confirmation does not match the recorded distribution"
            )
        return {**previous.result_json, "replayed": True}
    plan = preview_distribution(db, payload)
    if not payload.confirmed or payload.preview_hash != plan["preview_hash"]:
        raise DataError(
            "Preview the current rules and inventory before confirming distribution"
        )
    if len(payload.reason.strip()) < 3:
        raise DataError("A distribution reason is required")
    if not plan["allocated"]:
        raise DataError("No properties match this distribution")
    run_id = uuid.uuid4().hex
    result = {
        "run_id": run_id,
        "mode": payload.mode,
        "allocated": plan["allocated"],
        "leads_created": 0 if payload.mode == "sandbox" else plan["allocated"],
        "summaries": plan["summaries"],
        "replayed": False,
    }
    db.add(
        DistributionRun(
            id=run_id,
            mode=payload.mode,
            actor_id=actor_id,
            reason=payload.reason.strip(),
            preview_hash=payload.preview_hash,
            plan_json=plan,
            result_json=result,
        )
    )
    db.flush()
    blocked_numbers = {
        normalize_phone(lead.phone)
        for lead in db.query(Lead).filter(Lead.dnc.is_(True)).all()
        if lead.phone
    }
    for allocation in plan["allocations"]:
        row = db.get(CombinedProperty, allocation["property_id"])
        claimed = db.execute(
            update(CombinedProperty)
            .where(
                CombinedProperty.id == row.id,
                CombinedProperty.brokerage_id.is_(None),
                CombinedProperty.content_hash == allocation["content_hash"],
            )
            .values(brokerage_id=allocation["brokerage_id"], distribution_run_id=run_id)
        ).rowcount
        if claimed != 1:
            raise DataError("Inventory changed; preview distribution again")
        if payload.mode == "sandbox":
            continue
        data = row.data_json
        allowed = [
            phone
            for phone in data.get("phones", [])
            if not phone["dnc"] and phone["number"] not in blocked_numbers
        ]
        lead = Lead(
            user_id=allocation["recipient_id"],
            owner_name=data.get("owner"),
            phone=allowed[0]["number"] if allowed else None,
            property_address=data["address"],
            area=data.get("city"),
            signals=",".join(row.categories_json),
            source="provider_distribution",
            dnc=row.lead_status == "dnc" or bool(data.get("phones") and not allowed),
            details=json.dumps(lead_details_payload(data, row.sources_json, run_id)),
        )
        lead.score = score_lead(
            row.categories_json, bool(lead.phone), bool(lead.property_address)
        )
        db.add(lead)
        db.flush()
        row.lead_id = lead.id
    db.flush()
    return result


def combined_value(row):
    return {
        "id": row.id,
        "mode": row.mode,
        **row.data_json,
        "categories": row.categories_json,
        "lead_status": row.lead_status,
        "sources": row.sources_json,
        "brokerage_id": row.brokerage_id,
        "lead_id": row.lead_id,
    }
