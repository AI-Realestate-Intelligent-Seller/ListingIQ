import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "integration-data-test-key")

from app.batchdata import models as bd
from app.db import Base
from app.dealmachine import models as dm
from app.integration_data import service
from app.integration_data.models import CombinedProperty, DistributionRun
from app.models import Lead, PlatformAuditLog, User
from app.propertyradar import models as pr
from app.routes import integration_data as routes
from app.routes.integration_data import DistributionRequest, router
from app.routes.platform_admin import require_platform_admin


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        session.add_all(
            [
                User(
                    id=1,
                    email="admin@test.invalid",
                    hashed_password="unused",
                    role="platform_admin",
                    is_active=True,
                ),
                User(
                    id=2,
                    email="head-a@test.invalid",
                    hashed_password="unused",
                    role="hob",
                    brokerage_id="A",
                    brokerage_name="Brokerage A",
                    is_active=True,
                ),
                User(
                    id=3,
                    email="head-b@test.invalid",
                    hashed_password="unused",
                    role="hob",
                    brokerage_id="B",
                    brokerage_name="Brokerage B",
                    is_active=True,
                ),
                User(
                    id=4,
                    email="agent@test.invalid",
                    hashed_password="unused",
                    role="agent",
                    brokerage_id="C",
                    is_active=True,
                ),
                User(
                    id=5,
                    email="inactive@test.invalid",
                    hashed_password="unused",
                    role="hob",
                    brokerage_id="D",
                    is_active=False,
                ),
            ]
        )
        session.commit()
        yield session
    engine.dispose()


def batch_property(db, source_id, street="123 Main Street", mode="live", phones=None):
    row = bd.Property(
        provider="batchdata_sandbox" if mode == "sandbox" else "batchdata",
        provider_property_id=source_id,
        immutable_provider_snapshot={
            "_id": source_id,
            "address": {
                "street": street,
                "city": "Chicago",
                "state": "IL",
                "zip": "60601",
            },
            "owner": {"fullName": "Owner"},
        },
        operational_copy={
            "_quick_lists_run": "discovery",
            "_stages": {
                "contacts": {
                    "status": "completed",
                    "data": [{"persons": [{"phones": phones or []}]}],
                }
            },
        },
    )
    db.add(row)
    db.flush()
    db.add(bd.Membership(property_id=row.id, category="fsbo"))
    db.flush()
    return row


def request(mode="live", **kwargs):
    return DistributionRequest(
        mode=mode,
        rules=[
            {"brokerage_id": "A", "quantity": 1, "categories": ["fsbo"]},
            {"brokerage_id": "B", "quantity": 1, "lead_statuses": ["needs_review"]},
        ],
        **kwargs,
    )


def test_combine_deduplicates_providers_unions_categories_and_preserves_sources(db):
    original = batch_property(db, "BD1", phones=[{"number": "3125550100"}])
    raw = dict(original.immutable_provider_snapshot)
    radar = pr.Property(
        radar_id="PR1",
        first_seen_source="manual",
        current_provider_payload={
            "Address": "123 Main St",
            "City": "Chicago",
            "State": "IL",
            "Zip": "60601",
        },
    )
    category = pr.Category(key="vacant", label="Vacant", criteria_json={})
    db.add_all([radar, category])
    db.flush()
    db.add(pr.Membership(property_id=radar.id, category_id=category.id))
    db.add(
        pr.Phone(property_id=radar.id, normalized_phone="+13125550100", status="dnc")
    )
    db.add(
        dm.Property(
            provider_property_id="DM1",
            operational_json={"full_address": "124 Main St, Chicago, IL 60601"},
        )
    )
    db.flush()
    result = service.combine(db, "live")
    assert result["source_records"] == 3
    assert result["unique_properties"] == 2
    assert result["duplicates_removed"] == 1
    row = (
        db.query(CombinedProperty)
        .filter(CombinedProperty.property_key.like("123%"))
        .one()
    )
    assert row.categories_json == ["fsbo", "vacant"]
    assert {item["provider"] for item in row.sources_json} == {
        "batchdata",
        "propertyradar",
    }
    assert row.lead_status == "dnc"
    assert row.data_json["phones"] == [{"number": "+13125550100", "dnc": True}]
    assert original.immutable_provider_snapshot == raw
    assert service.combine(db, "live")["created"] == 0
    assert db.query(CombinedProperty).count() == 2


def test_units_and_incomplete_addresses_do_not_collapse(db):
    batch_property(db, "U1", "123 Main St Apt 1")
    batch_property(db, "U2", "123 Main Street Apt 2")
    db.add_all(
        [
            dm.Property(provider_property_id="EMPTY1", operational_json={}),
            dm.Property(provider_property_id="EMPTY2", operational_json={}),
        ]
    )
    db.flush()
    assert service.combine(db, "live")["unique_properties"] == 4
    assert len({row.property_key for row in db.query(CombinedProperty)}) == 4
    plan = service.preview_distribution(db, request())
    assert plan["allocated"] == 2
    assert plan["excluded"] == 2


def test_live_distribution_is_atomic_tenant_scoped_and_idempotent(db):
    batch_property(db, "BD1", phones=[{"number": "3125550100"}])
    batch_property(db, "BD2", "124 Main St")
    service.combine(db, "live")
    db.commit()
    plan = service.preview_distribution(db, request())
    assert db.query(Lead).count() == 0
    assert db.query(DistributionRun).count() == 0
    assert plan["allocated"] == 2
    payload = request(
        confirmed=True, reason="approved allocation", preview_hash=plan["preview_hash"]
    )
    result = service.distribute(db, payload, 1)
    db.commit()
    assert result["leads_created"] == 2
    assert {lead.user_id for lead in db.query(Lead)} == {2, 3}
    assert all(lead.source == "provider_distribution" for lead in db.query(Lead))
    assert service.distribute(db, payload, 1)["replayed"]
    assert db.query(Lead).count() == 2
    assert db.query(DistributionRun).count() == 1
    service.combine(db, "live")
    assert {row.brokerage_id for row in db.query(CombinedProperty)} == {"A", "B"}
    assert service.preview_distribution(db, request())["allocated"] == 0


def test_stale_preview_and_changed_rules_are_rejected(db):
    batch_property(db, "BD1")
    service.combine(db, "live")
    db.commit()
    plan = service.preview_distribution(db, request())
    row = db.query(CombinedProperty).one()
    row.content_hash = "changed"
    db.commit()
    with pytest.raises(service.DataError, match="Preview"):
        service.distribute(
            db,
            request(
                confirmed=True, reason="approved", preview_hash=plan["preview_hash"]
            ),
            1,
        )
    assert db.query(Lead).count() == 0
    assert db.query(DistributionRun).count() == 0
    current = service.preview_distribution(db, request())
    payload = request(
        confirmed=True, reason="approved", preview_hash=current["preview_hash"]
    )
    payload.rules[0].quantity = 2
    with pytest.raises(service.DataError, match="Preview"):
        service.distribute(db, payload, 1)


def test_existing_pool_properties_and_inactive_brokerages_are_excluded(db):
    batch_property(db, "BD1")
    db.add(Lead(user_id=3, property_address="123 Main St, Chicago IL 60601"))
    db.flush()
    service.combine(db, "live")
    plan = service.preview_distribution(db, request())
    assert plan["allocated"] == 0
    assert plan["excluded"] == 1
    assert {row["id"] for row in service.brokerage_rows(db)} == {"A", "B"}
    payload = request()
    payload.rules[0].brokerage_id = "D"
    with pytest.raises(service.DataError, match="active"):
        service.preview_distribution(db, payload)


def test_sandbox_inventory_is_separate_and_does_not_create_tenant_leads(db):
    batch_property(db, "LIVE1")
    batch_property(db, "TEST1", mode="sandbox")
    assert len(service.source_records(db, "batchdata", "live")) == 1
    assert len(service.source_records(db, "batchdata", "sandbox")) == 1
    service.combine(db, "live")
    service.combine(db, "sandbox")
    db.commit()
    plan = service.preview_distribution(db, request("sandbox"))
    result = service.distribute(
        db,
        request(
            "sandbox",
            confirmed=True,
            reason="sandbox test",
            preview_hash=plan["preview_hash"],
        ),
        1,
    )
    db.commit()
    assert result["allocated"] == 1
    assert result["leads_created"] == 0
    assert db.query(Lead).count() == 0
    assert db.query(CombinedProperty).filter_by(mode="live").one().brokerage_id is None


def test_routes_require_platform_role_and_save_audited_distribution(db):
    for route in router.routes:
        assert any(
            dependency.call is require_platform_admin
            for dependency in route.dependant.dependencies
        )
    for user_id in (4, 5):
        with pytest.raises(HTTPException) as error:
            require_platform_admin(db.get(User, user_id))
        assert error.value.status_code == 403
    actor = require_platform_admin(db.get(User, 1))
    batch_property(db, "BD1")
    db.commit()
    assert (
        routes.combine_data(routes.CombineRequest(mode="live"), db, actor)["created"]
        == 1
    )
    assert routes.source_data("batchdata", "live", 0, 1, db)["total"] == 1
    source = routes.source_data("batchdata", "live", 0, 1, db)["items"][0]
    assert source["table_data"]["property_id"] == "BD1"
    assert (
        routes.source_property("batchdata", source["source_record_id"], "live", db)[
            "data"
        ]["_id"]
        == "BD1"
    )
    with pytest.raises(HTTPException) as error:
        routes.source_property("batchdata", source["source_record_id"], "sandbox", db)
    assert error.value.status_code == 404
    plan = routes.preview(request(), db)
    payload = request(
        confirmed=True, reason="route validation", preview_hash=plan["preview_hash"]
    )
    assert routes.execute(payload, db, actor)["leads_created"] == 1
    assert routes.execute(payload, db, actor)["replayed"]
    assert db.query(PlatformAuditLog).count() == 2
    assert routes.history("live", 0, 25, db)["total"] == 1


def test_failed_claim_rolls_back_the_entire_distribution(db, monkeypatch):
    batch_property(db, "BD1")
    batch_property(db, "BD2", "124 Main Street")
    service.combine(db, "live")
    db.commit()
    plan = service.preview_distribution(db, request())
    payload = request(
        confirmed=True, reason="atomic distribution", preview_hash=plan["preview_hash"]
    )
    original_execute = db.execute
    claims = 0

    def fail_second_claim(statement, *args, **kwargs):
        nonlocal claims
        if getattr(statement, "is_update", False):
            claims += 1
            if claims == 2:
                return SimpleNamespace(rowcount=0)
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", fail_second_claim)
    with pytest.raises(HTTPException) as error:
        routes.execute(payload, db, db.get(User, 1))
    assert error.value.status_code == 409
    assert db.query(Lead).count() == 0
    assert db.query(DistributionRun).count() == 0
    assert all(row.brokerage_id is None for row in db.query(CombinedProperty))


def test_opted_out_phone_is_preserved_as_dnc_and_confirmation_is_required(db):
    batch_property(db, "BD1", phones=[{"number": "3125550100"}])
    db.add(
        Lead(
            user_id=2,
            property_address="99 Other St, Chicago IL",
            phone="+13125550100",
            dnc=True,
        )
    )
    db.flush()
    service.combine(db, "live")
    db.commit()
    row = db.query(CombinedProperty).one()
    assert row.lead_status == "dnc"
    assert row.data_json["phones"][0]["dnc"]
    plan = service.preview_distribution(db, request())
    with pytest.raises(service.DataError, match="explicit confirmation"):
        service.distribute(
            db,
            request(reason="needs confirmation", preview_hash=plan["preview_hash"]),
            1,
        )
