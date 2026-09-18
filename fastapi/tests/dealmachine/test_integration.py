import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "dealmachine-test-key")

from app.db import Base
from app.dealmachine.client import ProviderResponse
from app.dealmachine.config import (
    ContactEnrichmentRequest,
    DetailsRequest,
    FilterValue,
    SearchRequest,
    Settings,
)
from app.dealmachine.models import (
    ContactPoint,
    EnrichmentJob,
    Execution,
    Membership,
    Property,
    Snapshot,
)
from app.dealmachine.service import Service, UnsafeOperation, initialize
from app.routes.dealmachine import router


class FakeClient:
    def __init__(self):
        self.calls = []
        self.properties = [
            {"dm_property_id": "DM1", "full_address": "1 Main"},
            {"dm_property_id": "DM2", "full_address": "2 Main"},
        ]

    def response(self, data):
        return ProviderResponse(data, "req-test", {"x-request-id": "req-test"})

    def account(self):
        self.calls.append(("account", None))
        return self.response(
            {"data": {"organization": {"id": 1}, "plan": {"name": "Pro"}}}
        )

    def filters(self):
        self.calls.append(("filters", None))
        return self.response(
            {
                "data": [
                    {
                        "filter_id": "listing_status",
                        "name": "Listing Status",
                        "allowed_operators": ["contains_any"],
                    }
                ]
            }
        )

    def fields(self):
        self.calls.append(("fields", None))
        return self.response(
            {"data": [{"field_id": "estimated_value", "name": "Estimated Value"}]}
        )

    def usage(self):
        self.calls.append(("usage", None))
        return self.response(
            {
                "data": {
                    "total_available": 10000,
                    "billing_cycle_start": "2026-09-01T00:00:00Z",
                    "billing_cycle_end": "2026-10-01T00:00:00Z",
                }
            }
        )

    def property_count(self, body):
        self.calls.append(("count", body))
        return self.response({"total_properties": 2, "total_results": 2})

    def property_estimate(self, body):
        self.calls.append(("estimate", body))
        return self.response(
            {
                "estimated_credits": {
                    "this_page": 2,
                    "total_all_pages": 2,
                    "breakdown": {"properties": 2, "people": 0},
                }
            }
        )

    def property_search(self, body):
        self.calls.append(("search", body))
        return self.response(
            {
                "data": self.properties,
                "credits": {"used": 2, "properties": 2, "people": 0, "deduplicated": 0},
            }
        )

    def property_details(self, body):
        self.calls.append(("details", body))
        rows = []
        for identifier in body["ids"]:
            rows.append(
                {
                    "dm_property_id": identifier,
                    "contacts": [
                        {
                            "dm_person_id": "PERSON-SHARED",
                            "phones": [
                                {"number": "+1 555 000 1111", "do_not_call": False},
                                {"number": "+1 555 000 2222"},
                            ],
                            "emails": [{"address": "Owner@Example.com"}],
                        }
                    ],
                }
            )
        return self.response(
            {
                "data": rows,
                "credits": {
                    "used": len(rows) * 2,
                    "properties": len(rows),
                    "people": len(rows),
                },
            }
        )

    def contact_enrichment(self, body):
        self.calls.append(("contacts", body))
        rows = []
        for identifier in body["ids"]:
            rows.append(
                {
                    "dm_property_id": identifier,
                    "contacts": [
                        {
                            "dm_person_id": "PERSON-SHARED",
                            "phones": [
                                {"number": "+1 555 000 1111", "do_not_call": False},
                                {"number": "+1 555 000 2222"},
                            ],
                            "emails": [{"address": "Owner@Example.com"}],
                        }
                    ],
                }
            )
        return self.response(
            {
                "data": rows,
                "credits": {
                    "used": len(rows) * 2,
                    "properties": len(rows),
                    "people": len(rows),
                },
            }
        )


@pytest.fixture
def service(tmp_path, monkeypatch):
    from app import models as app_models  # noqa: F401
    from app.dealmachine import models as dm_models

    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setenv("DEALMACHINE_STORAGE_ROOT", str(tmp_path / "archive"))
    monkeypatch.setenv("DEALMACHINE_ENCRYPTION_KEY", "x" * 40)
    with maker() as db:
        initialize(db)
        row = db.query(dm_models.Integration).one()
        row.connection_status = "connected"
        db.commit()
        yield Service(db, FakeClient()), db
    engine.dispose()


def test_api_key_encryption_and_masking(service):
    subject, db = service
    settings = Settings(**{**subject.settings, "api_key": "dm_sk_live_secret1234"})
    result = subject.save_settings(settings, None)
    db.commit()
    assert result["credential"]["masked"].endswith("1234")
    assert "secret1234" not in subject.row.encrypted_api_key
    assert subject.api_key == "dm_sk_live_secret1234"


def test_metadata_cache_and_estimate_enforcement(service):
    subject, db = service
    first = subject.metadata("filter", True, None)
    db.commit()
    second = subject.metadata("filter", False, None)
    assert first["cached"] is False and second["cached"] is True
    request = SearchRequest(
        filters=[
            FilterValue(
                filter_id="listing_status", operator="contains_any", value=["expired"]
            )
        ],
        page=9,
        per_page=20,
    )
    subject.estimate(request, None)
    body = subject.client.calls[-1][1]
    assert (
        body["estimate_cost"] is True
        and body["contact_audience"] == "none"
        and body["anchor"] == "properties"
    )


def test_property_dedup_memberships_changes_and_immutable_snapshots(service):
    subject, db = service
    categories = (
        db.query(__import__("app.dealmachine.models", fromlist=["Category"]).Category)
        .limit(2)
        .all()
    )
    request = SearchRequest(
        category_ids=[row.key for row in categories],
        confirmed=True,
        reason="approved test",
        per_page=20,
    )
    first = subject.search(request, None)
    db.commit()
    assert first["processed"]["new"] == 2 and first["processed"]["queued"] == 0
    assert (
        db.query(Property).count() == 2
        and db.query(Membership).count() == 4
        and db.query(EnrichmentJob).count() == 0
    )
    subject.client.properties[0] = {
        "dm_property_id": "DM1",
        "full_address": "1 Changed",
    }
    second = subject.search(request, None)
    db.commit()
    assert (
        second["processed"]["new"] == 0
        and second["processed"]["changed"] == 1
        and db.query(Snapshot).count() == 4
    )
    snapshot = db.query(Snapshot).first()
    snapshot.raw_payload = {"tampered": True}
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()


def test_details_and_contact_enrichment_are_separate_paid_calls(service):
    subject, db = service
    with pytest.raises(UnsafeOperation):
        subject.details(
            DetailsRequest(
                dm_property_ids=["UNKNOWN"], confirmed=True, reason="approved details"
            ),
            None,
        )
    subject.search(SearchRequest(confirmed=True, reason="approved search"), None)
    db.commit()
    details_calls = sum(name == "details" for name, _ in subject.client.calls)
    with pytest.raises(UnsafeOperation):
        subject.details(DetailsRequest(dm_property_ids=["DM1"]), None)
    assert sum(name == "details" for name, _ in subject.client.calls) == details_calls
    details = subject.details(
        DetailsRequest(
            dm_property_ids=["DM1", "DM2"], confirmed=True, reason="approved details"
        ),
        None,
    )
    db.commit()
    assert details["processed"]["contacts"] == 0
    assert subject.client.calls[-1][0] == "details"
    assert subject.client.calls[-1][1]["contact_audience"] == "none"
    assert db.query(ContactPoint).count() == 0
    result = subject.enrich_contacts(
        ContactEnrichmentRequest(
            dm_property_ids=["DM1", "DM2"], confirmed=True, reason="approved contacts"
        ),
        None,
    )
    db.commit()
    assert result["processed"]["contacts"] == 2
    assert subject.client.calls[-1][0] == "contacts"
    assert db.query(ContactPoint).filter_by(kind="phone").count() == 2
    assert db.query(ContactPoint).filter_by(kind="email").count() == 1


def test_credit_limit_blocks_before_provider_call(service):
    subject, db = service
    values = subject.settings
    values["limits"] = {**values["limits"], "per_run_property_limit": 1}
    subject.row.settings_json = values
    db.commit()
    with pytest.raises(UnsafeOperation):
        subject.search(
            SearchRequest(confirmed=True, reason="approved search", per_page=20), None
        )
    assert not any(name == "search" for name, _ in subject.client.calls)


def test_search_does_not_queue_enrichment_and_manual_enrichment_is_recorded(service):
    subject, db = service
    subject.search(SearchRequest(confirmed=True, reason="approved search"), None)
    db.commit()
    assert db.query(EnrichmentJob).count() == 0
    subject.enrich_contacts(
        ContactEnrichmentRequest(
            dm_property_ids=["DM1"], confirmed=True, reason="approved contacts"
        ),
        None,
    )
    db.commit()
    assert db.query(EnrichmentJob).filter_by(status="completed").count() == 1
    subject.new_run("scheduled", None, [], {}, "scheduled:expired:2026-09-10")
    db.commit()
    with pytest.raises(UnsafeOperation):
        subject.new_run("scheduled", None, [], {}, "scheduled:expired:2026-09-10")


def test_execution_response_is_append_only(service):
    subject, db = service
    subject.estimate(SearchRequest(), None)
    db.commit()
    execution = db.query(Execution).filter_by(status="succeeded").first()
    execution.response_json = {"tampered": True}
    with pytest.raises(ValueError):
        db.commit()
    db.rollback()


def test_every_required_route_is_dedicated():
    expected = {
        ("POST", "/connection/test"),
        ("GET", "/filters"),
        ("GET", "/fields"),
        ("GET", "/usage"),
        ("POST", "/properties/count"),
        ("POST", "/properties/estimate"),
        ("POST", "/properties/search"),
        ("POST", "/properties/details"),
        ("POST", "/contacts/enrich"),
        ("POST", "/lists"),
        ("GET", "/lists/{list_id}"),
        ("POST", "/lists/{list_id}/items"),
        ("DELETE", "/lists/{list_id}/items"),
        ("POST", "/activity/search"),
        ("GET", "/activity/{activity_id}"),
        ("POST", "/properties/export"),
        ("GET", "/history"),
        ("GET", "/history/{run_id}"),
        ("GET", "/files/{file_id}/download"),
        ("GET", "/settings"),
        ("PUT", "/settings"),
        ("POST", "/settings/test"),
    }
    actual = {
        (method, route.path) for route in router.routes for method in route.methods
    }
    assert expected <= actual
    assert not any(route.path.startswith("/sync/") for route in router.routes)
    assert ("POST", "/enrichment/queue") not in actual
    assert ("POST", "/enrichment/run") not in actual
    assert not any(route.path.startswith("/enrichment/") for route in router.routes)
    assert not any("proxy" in route.path for route in router.routes)
    assert all(route.dependant.dependencies for route in router.routes)
