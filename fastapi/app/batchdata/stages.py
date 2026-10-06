"""Explicit contact-enrichment stage after combined property search."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func

from .models import ApiCall, Membership, Property, Reservation, Run


def _contact_results(response):
    """Normalize BatchData's supported skip-trace response envelopes."""
    from .service import UnsafeOperation

    result = response.get("result", response.get("results"))
    if isinstance(result, dict):
        data = result.get("data")
        if data is None and isinstance(result.get("persons"), list):
            data = [result]
        elif isinstance(data, dict):
            data = [data]
    elif isinstance(result, list):
        data = result
    else:
        data = None
    if not isinstance(data, list) or any(not isinstance(row, dict) for row in data):
        raise UnsafeOperation(
            "BatchData contact response has malformed results; reconcile before retrying"
        )
    return data


def _same_address(returned, expected):
    if not isinstance(returned, dict):
        return False
    return all(
        str(returned.get(key, "")).strip().casefold() == str(value).strip().casefold()
        for key, value in expected.items()
    )


def selected_stage(service, product, payload):
    from .service import UnsafeOperation, _redact, digest, property_table_row

    if product != "contact_enrichment":
        raise UnsafeOperation(
            "Only contact enrichment is available after property search"
        )
    if not service.config["contactEnrichmentEnabled"]:
        raise UnsafeOperation("Enable contact enrichment access first")
    properties = (
        service.db.query(Property)
        .filter(
            Property.id.in_(payload.property_ids),
            Property.provider == service.property_provider,
        )
        .order_by(Property.id)
        .all()
    )
    if len(properties) != len(payload.property_ids):
        raise UnsafeOperation(
            "Select only saved BatchData properties from the current API mode"
        )
    calls = []
    unit = Decimal(str(service.config["contactEnrichmentUnitCost"]))
    for prop in properties:
        current = prop.operational_copy or {}
        if not (current.get("_property_search_run") or current.get("_quick_lists_run")):
            raise UnsafeOperation(
                "Run Property Search first; this property has no saved search result"
            )
        stage = current.get("_stages", {}).get("contacts", {})
        if stage.get("status") or prop.skiptrace_status in {"matched", "no_match"}:
            raise UnsafeOperation(
                "Contact enrichment was already requested for a selected property; view its saved data or reconcile the earlier call"
            )
        raw_address = prop.immutable_provider_snapshot.get("address", {})
        address = {
            key: raw_address[key]
            for key in ("street", "city", "state", "zip")
            if isinstance(raw_address.get(key), str) and raw_address[key].strip()
        }
        if not all(address.get(key) for key in ("street", "city", "state")):
            raise UnsafeOperation(
                "Selected property is missing street, city or state required by BatchData skip trace"
            )
        archive = current.get("_archive", {})
        membership = (
            service.db.query(Membership)
            .filter(Membership.property_id == prop.id)
            .order_by(Membership.id)
            .first()
        )
        archive_session_id = (
            archive.get("session_id")
            or current.get("_property_search_run")
            or current.get("_quick_lists_run")
        )
        calls.append(
            {
                "property_id": prop.id,
                "provider_property_id": prop.provider_property_id,
                "category": archive.get("primary_category")
                or (membership.category if membership else "uncategorized"),
                "archive_session_id": archive_session_id,
                "endpoint": "/api/v1/property/skip-trace",
                "product": product,
                "request": {"requests": [{"propertyAddress": address}]},
                "estimated_cost": float(unit),
            }
        )
    plan = {
        "mode": service.api_mode,
        "archive_user_id": service.archive_user_id or "unknown",
        "configuration_version": service.config["configurationVersion"],
        "stage": "contacts",
        "calls": calls,
        "property_ids": [prop.id for prop in properties],
        "property_search_calls": 0,
        "maximum_skip_trace_calls": len(calls),
        "maximum_returned_rows": len(calls),
        "estimated_cost": float(unit * len(calls)),
        "contact_enrichment_enabled": True,
    }
    signature = digest(plan)
    plan["preview_hash"] = signature
    if not payload.confirmed:
        return service._preview_run(plan)
    if payload.preview_hash != signature:
        raise UnsafeOperation(
            "Selection or configuration changed; preview contact enrichment again before confirming"
        )
    if len(payload.reason.strip()) < 3:
        raise UnsafeOperation("Execution requires confirmation and a reason")
    if not service.row.enabled:
        raise UnsafeOperation("Enable BatchData before executing")

    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    spend, matches = (
        service.db.query(
            func.coalesce(func.sum(ApiCall.actual_cost), 0),
            func.coalesce(func.sum(ApiCall.returned_records), 0),
        )
        .filter(ApiCall.product == product, ApiCall.created_at >= start)
        .one()
    )
    pending_spend = Decimal(0)
    pending_matches = 0
    for pending_run, pending in (
        service.db.query(Run, Reservation)
        .join(Reservation, Reservation.run_id == Run.id)
        .filter(Reservation.status.in_(["reserved", "reconciliation_required"]))
        .all()
    ):
        if pending_run.call_plan.get("stage") == "contacts":
            pending_spend += Decimal(str(pending.amount))
            pending_matches += len(pending_run.call_plan.get("property_ids", []))
    if (
        Decimal(str(spend)) + pending_spend + unit * len(calls)
        > Decimal(str(service.config["skipTraceSpendCap"]))
        or int(matches) + pending_matches + len(calls)
        > service.config["monthlySkipTraceLimit"]
    ):
        raise UnsafeOperation(
            "Selection exceeds the contact enrichment spend or match limit"
        )

    run = Run(
        id=uuid.uuid4().hex,
        status="Approved",
        configuration_version=service.config["configurationVersion"],
        selected_categories=[],
        call_plan=plan,
        estimated_cost=unit * len(calls),
    )
    service.db.add(run)
    service.db.commit()
    reservation = service._reserve(run)
    actual = Decimal(0)
    returned = 0
    exported = []
    call = None
    prop = None
    try:
        for prop, planned in zip(properties, calls):
            current = dict(prop.operational_copy or {})
            stages = dict(current.get("_stages", {}))
            stages["contacts"] = {
                "status": "requested",
                "run_id": run.id,
                "mode": service.api_mode,
            }
            current["_stages"] = stages
            prop.operational_copy = current
            call = ApiCall(
                run_id=run.id,
                endpoint=planned["endpoint"],
                product=product,
                status="Running",
                request_json=planned["request"],
                estimated_cost=unit,
                actual_cost=0,
            )
            service.db.add(call)
            service._save_property_file(
                run.id,
                planned["category"],
                prop.provider_property_id,
                "contacts_request",
                planned["request"],
                archive_run_id=planned["archive_session_id"],
            )
            service.db.commit()
            response, request_id = service.client.contact_enrichment(planned["request"])
            call.response_json = _redact(response)
            call.request_id = request_id
            service._save_property_file(
                run.id,
                planned["category"],
                prop.provider_property_id,
                "contacts_response",
                response,
                archive_run_id=planned["archive_session_id"],
            )
            service.db.commit()

            data = _contact_results(response)
            if service.api_mode != "sandbox":
                expected = planned["request"]["requests"][0]["propertyAddress"]

                def matches_request(
                    row,
                    expected=expected,
                    provider_id=prop.provider_property_id,
                ):
                    returned_property = row.get("property") or {}
                    returned_id = returned_property.get("id") or returned_property.get(
                        "_id"
                    )
                    returned_address = (row.get("input") or {}).get(
                        "propertyAddress"
                    ) or row.get("propertyAddress")
                    persons = row.get("persons") or []
                    if not returned_address and persons:
                        returned_address = persons[0].get("propertyAddress")
                    return str(returned_id) == provider_id or _same_address(
                        returned_address, expected
                    )

                if len(data) > 1 or any(not matches_request(row) for row in data):
                    raise UnsafeOperation(
                        "Contact response cannot be matched to the selected request; reconcile before retrying"
                    )
            has_data = any(row.get("persons") for row in data)
            charge = (
                Decimal(0) if service.api_mode == "sandbox" or not has_data else unit
            )
            actual += charge
            returned += int(has_data)
            call.actual_cost = charge
            call.returned_records = int(has_data)
            call.status = "Completed"
            current = dict(prop.operational_copy)
            stages = dict(current["_stages"])
            stages["contacts"] = {
                "status": "completed" if has_data else "no_match",
                "run_id": run.id,
                "mode": service.api_mode,
                "request_id": request_id,
                "data": _redact(data),
            }
            current["_stages"] = stages
            prop.operational_copy = current
            prop.skiptrace_status = "matched" if has_data else "no_match"
            exported.append(
                {
                    "source_property_id": prop.id,
                    "source_provider_id": prop.provider_property_id,
                    "mode": service.api_mode,
                    "stage": "contacts",
                    "data": _redact(data),
                }
            )
            service._save_property_file(
                run.id,
                planned["category"],
                prop.provider_property_id,
                "contacts",
                {
                    "source_property_id": prop.id,
                    "source_provider_id": prop.provider_property_id,
                    "mode": service.api_mode,
                    "request_id": request_id,
                    "status": "completed" if has_data else "no_match",
                    "data": _redact(data),
                },
                archive_run_id=planned["archive_session_id"],
            )
            service.db.commit()
        run.status = "Completed"
        run.actual_cost = actual
        run.returned_records = returned
        run.unique_properties = len(properties)
        run.duplicate_properties = 0
        reservation.amount = actual
        reservation.status = "reconciled"
        service.row.connection_status = (
            "sandbox_verified" if service.api_mode == "sandbox" else "connected"
        )
        service.row.last_error = None
        service.db.commit()
        value = service.run_value(run)
        value["properties"] = [
            property_table_row(prop.immutable_provider_snapshot) for prop in properties
        ]
        return value
    except Exception as error:
        service.db.rollback()
        run = service.db.get(Run, run.id)
        run.status = "Failed"
        run.error_message = str(error)[:2000]
        service.row.last_error = str(error)[:2000]
        if call is not None and call.id:
            failed_call = service.db.get(ApiCall, call.id)
            if failed_call.status == "Running":
                failed_call.status = "Failed"
                failed_call.error_message = str(error)[:2000]
        if prop is not None:
            current = dict(prop.operational_copy or {})
            stages = dict(current.get("_stages", {}))
            if stages.get("contacts", {}).get("status") == "requested":
                stages["contacts"] = {
                    **stages["contacts"],
                    "status": "reconciliation_required",
                }
                current["_stages"] = stages
                prop.operational_copy = current
        reservation = service.db.query(Reservation).filter_by(run_id=run.id).one()
        reservation.status = "reconciliation_required"
        service.db.commit()
        raise
