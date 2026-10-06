import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "batchdata-test-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.batchdata.config import (
    CATEGORIES,
    PROVIDER_QUICK_LISTS,
    UNSUPPORTED,
    Configuration,
    ContactEnrichmentRequest,
    ProductRequest,
)
from app.batchdata.models import (
    ApiCall,
    Membership,
    Property,
    Reservation,
    Run,
    SavedFile,
)
from app.batchdata.service import Service, UnsafeOperation, _rows, initialize
from app.db import Base


def test_selected_stage_accepts_selection_across_more_than_100_records():
    ids = list(range(1, 122))
    payload = ContactEnrichmentRequest(property_ids=ids)
    assert payload.property_ids == ids


@pytest.mark.parametrize(
    "payload",
    [
        {"results": {"properties": [{"id": "P1"}]}},
        {"data": {"results": {"properties": [{"id": "P1"}]}}},
        {"results": [{"id": "P1"}]},
        {"properties": [{"id": "P1"}]},
        {"data": {"properties": [{"id": "P1"}]}},
    ],
)
def test_parse_property_results(payload):
    assert _rows(payload) == [{"id": "P1"}]


def test_parse_empty_nested_property_results():
    assert _rows({"results": {"properties": []}}) == []


@pytest.mark.parametrize(
    "payload",
    [
        {"status": {"code": 200, "text": "OK"}},
        {"results": {"properties": {"id": "P1"}}},
        {"results": {"properties": [None]}},
    ],
)
def test_reject_missing_or_malformed_property_results(payload):
    with pytest.raises(UnsafeOperation):
        _rows(payload)


class FakeClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body):
        self.calls.append((method, path, body))
        if path.endswith("skip-trace"):
            return {
                "results": {
                    "persons": [
                        {
                            "name": {"full": "Owner"},
                            "propertyAddress": body["requests"][0]["propertyAddress"],
                        }
                    ]
                }
            }, "skip-request"
        category = body["searchCriteria"]["quickList"]
        ids = ["P1", "P2"] if category == PROVIDER_QUICK_LISTS["fsbo"] else ["P2", "P3"]
        return {
            "results": [
                {
                    "_id": value,
                    "address": {
                        "hash": f"hash-{value}",
                        "formatted": f"{value} Main St",
                        "street": f"{value} Main St",
                        "city": "Chicago",
                        "state": "IL",
                        "zip": "60601",
                    },
                }
                for value in ids
            ]
        }, f"search-{category}"

    def property_search(self, body):
        return self.request("POST", "/api/v1/property/search", body)

    def contact_enrichment(self, body):
        return self.request("POST", "/api/v1/property/skip-trace", body)


@pytest.mark.parametrize("combination,expected_unique", [("OR", 3), ("AND", 1), (None, 3)])
def test_normal_product_flow_uses_current_fields_in_sandbox(
    tmp_path, monkeypatch, caplog, combination, expected_unique
):
    monkeypatch.setenv("BATCHDATA_API_MODE", "sandbox")
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    engine = create_engine(f"sqlite:///{tmp_path}/sandbox-product.db")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as db:
        service = Service(db, FakeClient(), archive_user_id=42)
        service.save_config(
            Configuration(
                enabled=True,
                locations=["Chicago, IL"],
                selectedCategories=["fsbo", "vacant"],
            )
        )
        payload = ProductRequest(
            selected_categories=["fsbo", "vacant"],
            locations=["Chicago, IL", "Austin, TX"],
            rows_per_category=10,
            combination=combination,
        )
        with caplog.at_level(__import__("logging").INFO):
            preview = service.property_search(payload)
        messages = [record.getMessage() for record in caplog.records]
        assert any("event=batchdata.plan.created" in message for message in messages)
        assert any(
            "event=batchdata.preview.ready" in message
            and "provider_calls_executed=0" in message
            and "archive_writes=0" in message
            for message in messages
        )
        assert service.client.calls == []
        assert preview["call_plan"]["property_search_calls"] == 4
        assert preview["call_plan"]["maximum_returned_rows"] == 20
        result = service.property_search(
            payload.copy(update={"confirmed": True, "reason": "normal sandbox test"})
        )
        assert result["status"] == "Completed"
        assert result["actual_cost"] == 0
        assert result["unique_properties"] == expected_unique
        assert len(result["provider_calls"]) == 4
        assert db.query(SavedFile).count() == expected_unique * 4
        assert db.query(SavedFile).filter_by(kind="normalized").count() == expected_unique
        import json

        property_files = list((tmp_path / "archive").rglob("property.json"))
        assert len(property_files) == expected_unique
        assert all("/user=42/" in str(path) for path in property_files)
        assert all("/properties/category=" in str(path) for path in property_files)
        assert all("/property=" in str(path) for path in property_files)
        exported = [json.loads(path.read_text()) for path in property_files]
        assert all(row["address"]["street"].endswith("Main St") for row in exported)
        metadata = [
            json.loads(path.read_text())
            for path in (tmp_path / "archive").rglob("metadata.json")
        ]
        assert len(metadata) == expected_unique
        assert all(row["user_id"] == 42 for row in metadata)
        from app.routes.batchdata import saved_file_content

        property_file = db.query(SavedFile).filter_by(kind="property").first()
        saved_content = saved_file_content(property_file.id, service)
        assert len(saved_content["properties"]) == 1
        assert saved_content["properties"][0]["property_id"]
        assert {row.provider for row in db.query(Property)} == {"batchdata_sandbox"}
        for _, _, request in service.client.calls:
            assert request["searchCriteria"]["query"] in payload.locations
            assert request["searchCriteria"]["quickList"] in {
                PROVIDER_QUICK_LISTS[key] for key in payload.selected_categories
            }
            assert request["options"] == {
                "take": 5,
                "skip": 0,
                "datasets": ["basic", "listing", "quicklist"],
            }
            assert "locations" not in request["searchCriteria"]
        ids = [row.id for row in db.query(Property)]
        monkeypatch.setenv("BATCHDATA_API_MODE", "live")
        with pytest.raises(UnsafeOperation, match="saved BatchData"):
            service.contact_enrichment(ContactEnrichmentRequest(property_ids=ids))
    engine.dispose()


@pytest.mark.parametrize("nested_search_results", [False, True])
def test_property_search_deduplicates_without_implicit_skip_trace(
    tmp_path, monkeypatch, nested_search_results
):
    monkeypatch.setenv("BATCHDATA_API_MODE", "live")
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    with maker() as db:
        row = initialize(db)
        config = Configuration(
            enabled=True,
            locations=["Chicago, IL"],
            selectedCategories=["fsbo", "vacant"],
            rowsPerCategory=20,
        )
        row.enabled = True
        row.configuration_json = config.dict()
        db.commit()
        provider = FakeClient()
        if nested_search_results:
            original_request = provider.request

            def nested_request(method, path, body):
                response, request_id = original_request(method, path, body)
                if path.endswith("search"):
                    response = {"results": {"properties": response["results"]}}
                return response, request_id

            provider.request = nested_request
        service = Service(db, provider)
        run = service.create_plan()
        assert run["call_plan"]["property_search_calls"] == 2
        assert run["call_plan"]["maximum_returned_rows"] == 40
        service.transition(run["id"], "Validated")
        service.transition(run["id"], "Approved")
        result = service.execute(run["id"])
        assert result["status"] == "Completed"
        assert result["returned_records"] == 4
        assert result["unique_properties"] == 3
        assert result["duplicate_properties"] == 1
        assert result["actual_cost"] > 0
        assert db.query(Property).count() == 3
        assert db.query(Membership).count() == 4
        assert db.query(ApiCall).count() == 2
        assert len(provider.calls) == 2
        assert all(not path.endswith("skip-trace") for _, path, _ in provider.calls)
        assert list((tmp_path / "archive").rglob("*.json"))
    engine.dispose()


def test_selected_contacts_use_verified_skip_trace_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCHDATA_API_MODE", "live")
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    engine = create_engine(f"sqlite:///{tmp_path}/stages.db")
    Base.metadata.create_all(engine)

    class StageClient(FakeClient):
        def request(self, method, path, body):
            if "requests" not in body:
                return super().request(method, path, body)
            self.calls.append((method, path, body))
            entry = body["requests"][0]
            return {
                "results": {
                    "persons": [
                        {
                            "propertyAddress": entry["propertyAddress"],
                            "name": {"full": "Contact Owner"},
                            "phoneNumbers": [{"number": "5551112222"}],
                        }
                    ]
                }
            }, "contact-request"

    with sessionmaker(bind=engine, autoflush=False)() as db:
        service = Service(db, StageClient(), archive_user_id=42)
        service.save_config(
            Configuration(
                enabled=True,
                locations=["Chicago, IL"],
                selectedCategories=["fsbo"],
            )
        )
        with pytest.raises(UnsafeOperation, match="saved BatchData"):
            service.contact_enrichment(ContactEnrichmentRequest(property_ids=[999]))
        service.property_search(
            ProductRequest(
                selected_categories=["fsbo"],
                locations=["Chicago, IL"],
                confirmed=True,
                reason="discovery",
            )
        )
        assert len(service.client.calls) == 1
        props = db.query(Property).order_by(Property.id).all()
        original = dict(props[0].immutable_provider_snapshot)
        selection = ContactEnrichmentRequest(property_ids=[props[0].id, props[0].id])
        preview = service.contact_enrichment(selection)
        assert len(service.client.calls) == 1
        assert preview["call_plan"]["maximum_skip_trace_calls"] == 1
        assert (
            preview["call_plan"]["calls"][0]["request"]["requests"][0][
                "propertyAddress"
            ]["street"]
            == "P1 Main St"
        )
        with pytest.raises(UnsafeOperation, match="preview"):
            service.contact_enrichment(
                selection.copy(update={"confirmed": True, "reason": "contacts"})
            )
        changed = Configuration(**{**service.config, "contactEnrichmentUnitCost": 0.08})
        service.save_config(changed)
        with pytest.raises(UnsafeOperation, match="preview"):
            service.contact_enrichment(
                selection.copy(
                    update={
                        "confirmed": True,
                        "reason": "contacts",
                        "preview_hash": preview["call_plan"]["preview_hash"],
                    }
                )
            )
        assert len(service.client.calls) == 1
        service.save_config(
            Configuration(**{**service.config, "contactEnrichmentUnitCost": 0.07})
        )
        preview = service.contact_enrichment(selection)
        service.contact_enrichment(
            selection.copy(
                update={
                    "confirmed": True,
                    "reason": "contacts",
                    "preview_hash": preview["call_plan"]["preview_hash"],
                }
            )
        )
        assert len(service.client.calls) == 2
        assert db.query(Property).count() == 2
        assert props[0].immutable_provider_snapshot == original
        assert props[0].skiptrace_status == "matched"
        assert props[1].skiptrace_status != "matched"
        property_folder = next((tmp_path / "archive").rglob("property=P1"))
        assert {path.name for path in property_folder.iterdir()} >= {
            "property.json",
            "normalized.json",
            "metadata.json",
            "search_requests.json",
            "contacts_request.json",
            "contacts_response.json",
            "contacts.json",
        }
        with pytest.raises(UnsafeOperation, match="already requested"):
            service.contact_enrichment(selection)
        assert len(service.client.calls) == 2
        with pytest.raises(ValueError, match="immutable"):
            db.query(ApiCall).filter_by(
                product="contact_enrichment"
            ).one().response_json = {"changed": True}
        service.property_search(
            ProductRequest(
                selected_categories=["fsbo"],
                locations=["Chicago, IL"],
                confirmed=True,
                reason="repeat discovery",
            )
        )
        reused_contacts = list(
            (tmp_path / "archive").rglob("property=P1/contacts.json")
        )
        assert len(reused_contacts) == 2
        assert any(
            __import__("json").loads(path.read_text()).get("reused_from_run")
            for path in reused_contacts
        )
        pending = Run(
            id="unknown-contact",
            status="Failed",
            configuration_version=service.config["configurationVersion"],
            selected_categories=[],
            call_plan={"stage": "contacts", "property_ids": [999]},
            estimated_cost=175,
        )
        db.add(pending)
        db.add(
            Reservation(run_id=pending.id, amount=175, status="reconciliation_required")
        )
        db.commit()
        other = ContactEnrichmentRequest(property_ids=[props[1].id])
        other_preview = service.contact_enrichment(other)
        with pytest.raises(UnsafeOperation, match="contact enrichment spend"):
            service.contact_enrichment(
                other.copy(
                    update={
                        "confirmed": True,
                        "reason": "contacts",
                        "preview_hash": other_preview["call_plan"]["preview_hash"],
                    }
                )
            )
        assert len(service.client.calls) == 3
    engine.dispose()


def test_short_sale_remains_disabled():
    try:
        Configuration(selectedCategories=["short_sale"])
    except ValueError as error:
        assert "Short Sale" in str(error)
    else:
        raise AssertionError("Short Sale must remain unsupported")


def test_internal_categories_map_to_batchdata_quick_list_values():
    assert PROVIDER_QUICK_LISTS["fsbo"] == "for-sale-by-owner"
    assert PROVIDER_QUICK_LISTS["pre_foreclosure"] == "preforeclosure"
    assert PROVIDER_QUICK_LISTS["lis_pendens"] == "notice-of-lis-pendens"


def test_categories_without_a_provider_quick_list_are_not_batchdata_categories():
    for key in ("withdrawn", "probate", "divorce", "bankruptcy", "reo", "foreclosure"):
        assert key not in CATEGORIES
        with pytest.raises(ValueError, match="Unknown categories"):
            Configuration(selectedCategories=[key])
    assert UNSUPPORTED == {"short_sale"}


def test_initialize_repairs_legacy_short_sale_seed(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/legacy.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    with maker() as db:
        row = initialize(db)
        values = dict(row.configuration_json)
        values["selectedCategories"] = [*values["selectedCategories"], "short_sale"]
        row.configuration_json = values
        db.commit()
        repaired = initialize(db)
        assert "short_sale" not in repaired.configuration_json["selectedCategories"]
    engine.dispose()


def test_search_and_contact_have_isolated_preview_and_execution(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path}/products.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    with maker() as db:
        row = initialize(db)
        config = Configuration(
            enabled=True,
            locations=["Chicago, IL"],
            selectedCategories=["fsbo"],
        )
        row.enabled = True
        row.configuration_json = config.dict()
        db.commit()
        provider = FakeClient()
        service = Service(db, provider)
        payload = ProductRequest(
            selected_categories=["fsbo"],
            locations=["Chicago, IL"],
        )
        preview = service.property_search(payload)
        assert preview["status"] == "Preview"
        assert preview["call_plan"]["calls"][0]["product"] == "property_search"
        assert preview["estimated_cost"] == 2.4
        assert provider.calls == []
        executed = service.property_search(
            ProductRequest(
                selected_categories=["fsbo"],
                locations=["Chicago, IL"],
                confirmed=True,
                reason="manual product test",
            )
        )
        assert executed["status"] == "Completed"
        assert {call.product for call in db.query(ApiCall).all()} == {"property_search"}
        property_id = db.query(Property.id).first()[0]
        contact_preview = service.contact_enrichment(
            ContactEnrichmentRequest(property_ids=[property_id])
        )
        assert contact_preview["status"] == "Preview"
        assert contact_preview["call_plan"]["property_ids"] == [property_id]
    engine.dispose()
