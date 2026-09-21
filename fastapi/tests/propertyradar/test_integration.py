import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app.auth import create_access_token
from app.models import User
from app.propertyradar.config import (
    Configuration,
    ContactEnrichmentRequest,
    PropertyDetailsRequest,
    PropertySearchRequest,
    cycle,
    public_https,
)
from app.propertyradar.models import (
    Category,
    Email,
    IntegrationConfig,
    Membership,
    Phone,
    Property,
    ProviderList,
    SkiptraceJob,
    Snapshot,
    Usage,
    WebhookEvent,
)
from app.propertyradar.service import Service, UnsafeOperation, digest, operation_lock


def event(db, radar="P1", trigger="New Match", **kwargs):
    payload = {"RadarID": radar, "TriggerType": trigger, **kwargs}
    row = WebhookEvent(
        radar_id=radar,
        trigger_type=trigger,
        payload_hash=digest(payload),
        raw_payload=payload,
        list_name="Expired",
        change_1=kwargs.get("Change1"),
    )
    db.add(row)
    db.commit()
    return row


def prop(db, radar="P1"):
    row = Property(
        radar_id=radar, first_seen_source="initial", current_provider_payload={}
    )
    db.add(row)
    db.commit()
    return row


def lists(db, count=2):
    from app.propertyradar.categories import criteria

    cats = db.query(Category).limit(count).all()
    config = db.query(IntegrationConfig).one()
    config.configuration_json = {
        **config.configuration_json,
        "selected_categories": [c.key for c in cats],
    }
    db.commit()
    for index, cat in enumerate(cats, 1):
        db.add(
            ProviderList(
                category_id=cat.id,
                provider_list_id=str(index),
                list_name=f"List {index}",
                criteria_json=criteria(cat.key, config.configuration_json),
            )
        )
    db.commit()


def test_initial_dedup_memberships_and_purchase_order(service, db, provider):
    with operation_lock(db):
        preview = service.preview_import()
        assert preview["unique_count"] == 3
        assert preview["total_before_deduplication"] == 4
        assert preview["duplicate_occurrences"] == 1
        assert all(c[2].get("Purchase") == 0 for c in provider.calls)
        assert [c[2]["Fields"] for c in provider.calls[:2]] == ["RadarID", "RadarID"]
        result = service.import_initial(preview["preview_id"])
    assert result["created"] == 3
    assert db.query(Property).count() == 3
    assert db.query(Membership).count() == 4
    assert db.query(Snapshot).count() == 3
    assert db.query(SkiptraceJob).count() == 0
    purchases = [c for c in provider.calls if c[2].get("Purchase") == 1]
    assert len(purchases) == 3
    assert sorted(c[3]["Criteria"][0]["value"][0] for c in purchases) == [
        "P1",
        "P2",
        "P3",
    ]
    with operation_lock(db):
        p2 = service.preview_import()
        assert p2["estimated_exports"] == 0
        service.import_initial(p2["preview_id"])
    assert len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 3


def test_existing_match_never_purchases_or_skiptraces(service, db, provider):
    prop(db)
    row = event(db)
    service.process_event(row)
    assert row.is_duplicate_property
    assert provider.calls == []
    assert db.query(SkiptraceJob).count() == 0


def test_new_match_and_retry_idempotency(service, db, provider):
    row = event(db)
    service.process_event(row)
    assert db.query(SkiptraceJob).count() == 1
    retry = event(db)
    service.process_event(retry)
    assert retry.is_retry_duplicate
    assert retry.processing_status == "retry_duplicate"
    assert len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 1


@pytest.mark.parametrize("trigger", ["Change", "New Record"])
def test_change_snapshots_without_skiptrace(service, db, provider, trigger):
    p = prop(db)
    row = event(db, trigger=trigger, Change1="Listing Status:Active:Expired")
    service.process_event(row)
    assert p.current_provider_payload["Listing Status"] == "Expired"
    assert p.current_provider_payload["_changes"][0]["old"] == "Active"
    assert db.query(Snapshot).count() == 1
    assert db.query(SkiptraceJob).count() == 0
    assert provider.calls == []


def test_multiple_contacts_and_shared_owner_across_properties(service, db, provider):
    for radar in ["P1", "P2"]:
        p = prop(db, radar)
        service.queue_skiptrace(p)
        db.commit()
        service.skiptrace(db.query(SkiptraceJob).filter_by(property_id=p.id).one())
    assert db.query(Phone).count() == 4
    assert db.query(Email).count() == 2
    assert db.query(Phone).filter_by(normalized_phone="+15555551234").count() == 2
    assert db.query(SkiptraceJob).filter_by(status="completed").count() == 2
    assert (
        len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 2
    )  # reused owner unlocks


@pytest.mark.parametrize(
    "kind,cap",
    [
        ("phone_unlock", "phone_limit"),
        ("email_unlock", "email_limit"),
        ("property_export", "export_limit"),
    ],
)
def test_local_allowance_cap(service, db, provider, kind, cap):
    service.row.configuration_json = {**service.config, cap: 1}
    db.commit()
    with operation_lock(db):
        service.purchase("one", "POST", "/v1/persons/a/Phone", {}, None, kind)
        with pytest.raises(UnsafeOperation):
            service.purchase("two", "POST", "/v1/persons/b/Phone", {}, None, kind)
    assert service.used(kind) == 1
    assert len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 1


@pytest.mark.parametrize("free,cost", [(0, "0"), (100, "0.01")])
def test_provider_overage_blocks_purchase(service, db, provider, free, cost):
    provider.free = free
    provider.cost = cost
    with operation_lock(db), pytest.raises(UnsafeOperation):
        service.purchase("bad", "POST", "/v1/persons/a/Phone", {}, None, "phone_unlock")
    assert not any(c[2].get("Purchase") == 1 for c in provider.calls)


def test_concurrent_workers_cannot_exceed_cap(factory, provider):
    with factory() as db:
        service = Service(db, provider)
        service.row.configuration_json = {**service.config, "phone_limit": 1}
        db.commit()
    locked = Event()
    release = Event()

    def first_worker():
        with factory() as db, operation_lock(db):
            locked.set()
            release.wait(3)
            Service(db, provider).purchase(
                "one", "POST", "/v1/persons/a/Phone", {}, None, "phone_unlock"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(first_worker)
        assert locked.wait(3)
        with factory() as db, pytest.raises(UnsafeOperation), operation_lock(db):
            Service(db, provider).purchase(
                "two", "POST", "/v1/persons/b/Phone", {}, None, "phone_unlock"
            )
        release.set()
        future.result()
    assert len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 1


def test_uncertain_purchase_never_retried(service, db, provider):
    from app.propertyradar.client import ProviderError

    provider.fail_purchase = True
    with operation_lock(db), pytest.raises(ProviderError):
        service.purchase("one", "POST", "/v1/persons/a/Phone", {}, None, "phone_unlock")
    assert service.used("phone_unlock") == 1
    provider.fail_purchase = False
    with operation_lock(db), pytest.raises(UnsafeOperation):
        service.purchase("one", "POST", "/v1/persons/a/Phone", {}, None, "phone_unlock")
    assert len([c for c in provider.calls if c[2].get("Purchase") == 1]) == 1


def test_webhook_secret_and_storage(client, db):
    payload = {"RadarID": "P1", "TriggerType": "New Match"}
    assert (
        client.post("/api/v1/webhooks/propertyradar", json=payload).status_code == 401
    )
    headers = {"Authorization": "Bearer " + "s" * 40}
    response = client.post(
        "/api/v1/webhooks/propertyradar", headers=headers, json=payload
    )
    assert response.status_code == 202
    assert db.query(WebhookEvent).one().processing_status == "pending"
    client.post("/api/v1/webhooks/propertyradar", headers=headers, json=payload)
    assert db.query(WebhookEvent).count() == 2
    assert (
        db.query(WebhookEvent)
        .order_by(WebhookEvent.id.desc())
        .first()
        .is_retry_duplicate
    )


def test_test_webhook_no_provider_even_enabled(client, service, db, provider):
    response = client.post(
        "/api/v1/webhooks/propertyradar",
        headers={"Authorization": "Bearer " + "s" * 40},
        json={"RadarID": "TEST-PROPERTY-001", "TriggerType": "New Match"},
    )
    assert response.status_code == 202
    row = db.query(WebhookEvent).one()
    service.process_event(row)
    assert row.is_test and row.processing_status == "test"
    assert provider.calls == []


def test_disabled_accepts_pending_and_stops_worker(client, service, db, provider):
    service.row.enabled = False
    db.commit()
    response = client.post(
        "/api/v1/webhooks/propertyradar",
        headers={"Authorization": "Bearer " + "s" * 40},
        json={"RadarID": "P1", "TriggerType": "New Match"},
    )
    assert response.status_code == 202
    assert service.work_once() is False
    assert db.query(WebhookEvent).one().processing_status == "pending"
    assert provider.calls == []


def test_invalid_payload_saved(client, db):
    response = client.post(
        "/api/v1/webhooks/propertyradar",
        headers={"Authorization": "Bearer " + "s" * 40},
        content="{invalid",
    )
    assert response.status_code == 202
    assert db.query(WebhookEvent).one().processing_status == "invalid"


def test_monitoring_per_list_limit(service, db, provider):
    lists(db)
    provider.population["1"] = [f"P{i}" for i in range(10001)]
    preview = service.validate_monitoring()
    assert not preview["safe"] and "10,000" in preview["reason"]
    assert not any(c[0] == "PATCH" for c in provider.calls)


def test_monitoring_union_limit_and_pagination(service, db, provider):
    lists(db, 5)
    provider.population = {
        str(i): [f"P{i}-{j}" for j in range(10000)] for i in range(1, 6)
    }
    preview = service.validate_monitoring()
    assert not preview["safe"] and preview["union_count"] == 50000
    assert any(c[2].get("Start") == 9000 for c in provider.calls)
    assert not any(c[0] == "PATCH" for c in provider.calls)


def test_monitoring_union_dedup_and_confirmation(service, db, provider):
    lists(db)
    preview = service.validate_monitoring()
    assert preview["safe"] and preview["union_count"] == 3
    service.monitoring(True, preview["preview_id"])
    assert db.query(ProviderList).filter_by(is_monitored=True).count() == 2
    service.monitoring(False)
    assert db.query(ProviderList).count() == 2
    assert db.query(ProviderList).filter_by(is_monitored=True).count() == 0


def test_management_requires_active_platform_admin(client, db):
    index_root = "/api/v1/platform-admin/integrations"
    root = "/api/v1/integrations/propertyradar"
    assert client.get(root).status_code == 401
    user = User(
        email="hob@test.com", hashed_password="unused", role="hob", is_active=True
    )
    db.add(user)
    db.commit()
    header = {"Authorization": "Bearer " + create_access_token({"user_id": user.id})}
    assert client.get(root, headers=header).status_code == 403
    user.role = "platform_admin"
    db.commit()
    assert client.get(root, headers=header).status_code == 200
    assert client.get(index_root, headers=header).status_code == 200
    assert (
        client.post(root + "/imports/run", headers=header, json={}).status_code == 404
    )
    assert (
        client.post(
            root + "/properties/details",
            headers=header,
            json={"radar_ids": ["P1"], "confirmed": False},
        ).status_code
        == 409
    )
    assert (
        client.put(
            root + "/config", headers=header, json={"phone_limit": 2451}
        ).status_code
        == 422
    )


def test_config_and_local_url_validation():
    assert (
        len(
            Configuration(
                selected_categories=["expired", "expired"]
            ).selected_categories
        )
        == 1
    )
    for url in ["http://public.example.com", "https://localhost", "https://127.0.0.1"]:
        assert not public_https(url)
    assert public_https("https://hooks.example.com/api/v1/webhooks/propertyradar")
    assert cycle(
        {"billing_cycle_start": "2026-01-31"}, __import__("datetime").date(2026, 2, 28)
    ) == ("2026-02-28", "2026-03-31")


def test_immutable_snapshots(service, db):
    p = prop(db)
    service.snapshot(p, {"RadarID": "P1"}, "initial")
    db.commit()
    snapshot = db.query(Snapshot).one()
    snapshot.raw_payload = {"tampered": True}
    with pytest.raises(ValueError, match="immutable"):
        db.commit()
    db.rollback()


def test_retired_paused_lists_do_not_block_narrower_geography(service, db, provider):
    from app.propertyradar.categories import criteria

    lists(db)
    original = db.query(ProviderList).first()
    category = db.get(Category, original.category_id)
    service.row.configuration_json = {
        **service.config,
        "city": "Evanston",
        "selected_categories": [category.key],
    }
    db.commit()
    db.add(
        ProviderList(
            category_id=category.id,
            provider_list_id="3",
            list_name="Narrowed",
            criteria_json=criteria(category.key, service.config),
        )
    )
    db.commit()
    provider.population["1"] = [f"P{i}" for i in range(10001)]
    provider.population["3"] = ["PNARROW"]
    preview = service.validate_monitoring()
    assert preview["safe"] and preview["counts"] == {"3": 1}
    original.is_monitored = True
    db.commit()
    assert not service.validate_monitoring()[
        "safe"
    ]  # Old active populations still count.
    service.pause_list(original.id)
    assert service.validate_monitoring()["safe"]


def test_all_categories_request_twenty_each_before_details(service, db, provider):
    from app.propertyradar.categories import CATEGORIES

    service.row.configuration_json = {
        **service.config,
        "selected_categories": list(CATEGORIES),
    }
    db.commit()
    identification_calls = []
    original = provider.request

    def request(method, path, params=None, body=None):
        if (params or {}).get("Fields") == "RadarID":
            identification_calls.append(params)
            return {"results": [{"RadarID": f"P{i}"} for i in range(20)]}, "ids"
        return original(method, path, params, body)

    provider.request = request
    preview = service.preview_import()
    assert len(identification_calls) == 21
    assert all(
        call["Limit"] == 20 and call["Purchase"] == 0 for call in identification_calls
    )
    assert preview["total_before_deduplication"] == 420
    assert preview["unique_count"] == 20
    assert preview["duplicate_occurrences"] == 400


def test_webhook_automation_merges_and_never_enables_provider_skiptrace(
    service, db, provider, monkeypatch
):
    lists(db, 1)
    monkeypatch.setenv("PROPERTYRADAR_WEBHOOK_SECRET", "s" * 40)
    monkeypatch.setenv(
        "PROPERTYRADAR_PUBLIC_WEBHOOK_URL",
        "https://hooks.example.com/api/v1/webhooks/propertyradar",
    )
    calls = []

    def request(method, path, params=None, body=None):
        calls.append((method, path, body))
        if path == "/v1/integrations/webhooks":
            return {"results": [{"WebhookID": 123}]}, "webhook"
        if method == "GET":
            return {
                "results": [
                    {
                        "isEnabled": 1,
                        "DailyEmailMemberIDs": "55",
                        "ExportToWebhookIDs": "99",
                        "PurchasePhoneOptions": "all",
                        "PurchaseEmailOptions": "all",
                    }
                ]
            }, "automation"
        return {"updateCount": 1}, "updated"

    provider.request = request
    service.register_webhook()
    put = next(body for method, path, body in calls if method == "PUT")
    assert put["DailyEmailMemberIDs"] == "55"
    assert set(put["ExportToWebhookIDs"].split(",")) == {"123", "99"}
    assert "PurchasePhoneOptions" not in put and "PurchaseEmailOptions" not in put
    assert "s" * 40 not in json.dumps(service.summary(), default=str)
    assert db.query(Usage).filter(Usage.response_payload.isnot(None)).count() == 0


def test_missing_cost_metadata_blocks_purchase(service, db, provider):
    provider.request = lambda *args: (
        {"resultCount": 1, "quantityFreeRemaining": 100},
        "missing-cost",
    )
    with operation_lock(db), pytest.raises(UnsafeOperation, match="missing"):
        service.purchase(
            "missing", "POST", "/v1/persons/owner/Phone", {}, None, "phone_unlock"
        )
    assert db.query(Usage).filter(Usage.operation_key.isnot(None)).count() == 0


def test_client_never_retries_purchase(monkeypatch):
    import requests

    from app.propertyradar.client import Client, ProviderError

    monkeypatch.setenv("PROPERTYRADAR_API_TOKEN", "private-test-token")
    calls = []

    def transport(*args, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout()

    monkeypatch.setattr("app.propertyradar.client.requests.request", transport)
    monkeypatch.setattr("app.propertyradar.client.time.sleep", lambda _: None)
    with pytest.raises(ProviderError):
        Client().request("POST", "/v1/persons/owner/Phone", {"Purchase": 1})
    assert len(calls) == 1
    assert calls[0]["headers"]["X-Radar-Access-Token"] == "private-test-token"
    assert calls[0]["allow_redirects"] is False
    with pytest.raises(ProviderError):
        Client().request("POST", "/v1/persons/owner/Phone", {"Purchase": 0})
    assert len(calls) == 4


def test_data_calls_are_separate_search_details_and_enrichment(service, db, provider):
    search = service.search_properties(
        PropertySearchRequest(
            category_ids=["expired"],
            state="IL",
            city="Chicago",
            rows_per_category=20,
        )
    )
    assert search["unique_count"] == 2
    assert all(call[2].get("Purchase") == 0 for call in provider.calls)

    details = service.fetch_property_details(
        PropertyDetailsRequest(
            radar_ids=["P1"],
            category_ids=["expired"],
            confirmed=True,
            reason="manual details test",
        )
    )
    assert details == {"requested": 1, "created": 1, "existing": 0}
    prop = db.query(Property).filter_by(radar_id="P1").one()
    assert prop.propertyradar_skiptrace_status == "not_requested"

    enriched = service.enrich_contacts(
        ContactEnrichmentRequest(
            radar_ids=["P1"],
            confirmed=True,
            reason="manual enrichment test",
        )
    )
    assert enriched == {"requested": 1, "completed": 1, "already_completed": 0}
    assert prop.propertyradar_skiptrace_status == "completed"
