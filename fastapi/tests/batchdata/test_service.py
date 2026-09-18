import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "batchdata-test-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.batchdata.config import Configuration, ContactEnrichmentRequest, ProductRequest
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
                "results": [
                    {"_id": item["_id"], "people": [{"name": "Owner"}]}
                    for item in body["properties"]
                ]
            }, "skip-request"
        category = body["searchCriteria"]["quickList"]
        ids = ["P1", "P2"] if category == "fsbo" else ["P2", "P3"]
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

    def quick_lists(self, body):
        return self.request("POST", "/api/v1/property/search", body)

    def basic_property(self, body):
        return self.request("POST", "/api/v1/property/search", body)

    def listing_data(self, body):
        return self.request("POST", "/api/v1/property/search", body)

    def pre_foreclosure(self, body):
        return self.request("POST", "/api/v1/property/search", body)

    def contact_enrichment(self, body):
        return self.request("POST", "/api/v3/property/skip-trace", body)


@pytest.mark.parametrize("combination,expected_unique", [("OR", 3), ("AND", 1)])
def test_normal_product_flow_uses_current_fields_in_sandbox(
    tmp_path, monkeypatch, combination, expected_unique
):
    monkeypatch.setenv("BATCHDATA_API_MODE", "sandbox")
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    engine = create_engine(f"sqlite:///{tmp_path}/sandbox-product.db")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as db:
        service = Service(db, FakeClient())
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
        preview = service.quick_lists(payload)
        assert service.client.calls == []
        assert preview["call_plan"]["property_search_calls"] == 4
        assert preview["call_plan"]["maximum_returned_rows"] == 20
        result = service.quick_lists(
            payload.copy(update={"confirmed": True, "reason": "normal sandbox test"})
        )
        assert result["status"] == "Completed"
        assert result["actual_cost"] == 0
        assert result["unique_properties"] == expected_unique
        assert len(result["provider_calls"]) == 4
        assert db.query(SavedFile).count() == 10
        import csv
        import json

        export = next((tmp_path / "archive").rglob("properties.json"))
        exported = json.loads(export.read_text())
        assert len(exported) == expected_unique
        csv_export = next((tmp_path / "archive").rglob("properties_csv.csv"))
        with csv_export.open(newline="") as stream:
            table = list(csv.DictReader(stream))
        assert len(table) == expected_unique
        assert table[0]["address"].endswith("Main St")
        assert {row.provider for row in db.query(Property)} == {"batchdata_sandbox"}
        for _, _, request in service.client.calls:
            assert request["searchCriteria"]["query"] in payload.locations
            assert request["searchCriteria"]["quickList"] in payload.selected_categories
            assert request["options"] == {"take": 5, "skip": 0}
            assert "dataTypes" not in request
            assert "locations" not in request["searchCriteria"]
        ids = [row.id for row in db.query(Property)]
        monkeypatch.setenv("BATCHDATA_API_MODE", "live")
        with pytest.raises(UnsafeOperation, match="saved BatchData"):
            service.contact_enrichment(ContactEnrichmentRequest(property_ids=ids))
    engine.dispose()


@pytest.mark.parametrize("nested_search_results", [False, True])
def test_quick_lists_deduplicate_without_implicit_skip_trace(
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


def test_selected_details_and_contacts_are_separate_cached_stages(
    tmp_path, monkeypatch
):
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
            if path.endswith("all-attributes"):
                key = entry["address"]["street"].split()[0]
                return {
                    "results": {
                        "properties": [
                            {"_id": key, "owner": {"fullName": "Detailed Owner"}}
                        ]
                    }
                }, "detail-request"
            return {
                "result": {
                    "data": [
                        {"input": entry, "persons": [{"fullName": "Contact Owner"}]}
                    ]
                }
            }, "contact-request"

    with sessionmaker(bind=engine, autoflush=False)() as db:
        service = Service(db, StageClient())
        service.save_config(
            Configuration(
                enabled=True,
                listingEnabled=True,
                locations=["Chicago, IL"],
                selectedCategories=["fsbo"],
            )
        )
        with pytest.raises(UnsafeOperation, match="saved BatchData"):
            service.basic_property(ContactEnrichmentRequest(property_ids=[999]))
        service.quick_lists(
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
        preview = service.basic_property(selection)
        assert len(service.client.calls) == 1
        assert preview["call_plan"]["property_lookup_calls"] == 1
        assert (
            preview["call_plan"]["calls"][0]["request"]["requests"][0]["address"][
                "street"
            ]
            == "P1 Main St"
        )
        with pytest.raises(UnsafeOperation, match="preview"):
            service.basic_property(
                selection.copy(update={"confirmed": True, "reason": "details"})
            )
        changed = Configuration(**{**service.config, "basicPropertyUnitCost": 0.02})
        service.save_config(changed)
        with pytest.raises(UnsafeOperation, match="preview"):
            service.basic_property(
                selection.copy(
                    update={
                        "confirmed": True,
                        "reason": "details",
                        "preview_hash": preview["call_plan"]["preview_hash"],
                    }
                )
            )
        assert len(service.client.calls) == 1
        preview = service.basic_property(selection)
        service.basic_property(
            selection.copy(
                update={
                    "confirmed": True,
                    "reason": "details",
                    "preview_hash": preview["call_plan"]["preview_hash"],
                }
            )
        )
        assert len(service.client.calls) == 2
        assert db.query(Property).count() == 2
        assert props[0].immutable_provider_snapshot == original
        assert (
            props[0].operational_copy["_stages"]["details"]["data"]["owner"]["fullName"]
            == "Detailed Owner"
        )
        assert "_stages" not in props[1].operational_copy
        with pytest.raises(UnsafeOperation, match="already requested"):
            service.listing_data(selection)
        contacts = service.contact_enrichment(selection)
        assert len(service.client.calls) == 2
        service.save_config(Configuration(**{**service.config, "skipTraceSpendCap": 0}))
        blocked = service.contact_enrichment(selection)
        with pytest.raises(UnsafeOperation, match="contact enrichment spend"):
            service.contact_enrichment(
                selection.copy(
                    update={
                        "confirmed": True,
                        "reason": "contacts",
                        "preview_hash": blocked["call_plan"]["preview_hash"],
                    }
                )
            )
        assert len(service.client.calls) == 2
        service.save_config(
            Configuration(**{**service.config, "skipTraceSpendCap": 175})
        )
        contacts = service.contact_enrichment(selection)
        service.contact_enrichment(
            selection.copy(
                update={
                    "confirmed": True,
                    "reason": "contacts",
                    "preview_hash": contacts["call_plan"]["preview_hash"],
                }
            )
        )
        assert len(service.client.calls) == 3
        assert props[0].skiptrace_status == "matched"
        assert props[1].skiptrace_status != "matched"
        with pytest.raises(UnsafeOperation, match="already requested"):
            service.contact_enrichment(selection)
        assert len(service.client.calls) == 3
        with pytest.raises(ValueError, match="immutable"):
            db.query(ApiCall).filter_by(
                product="basic_property"
            ).one().response_json = {"changed": True}
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


def test_each_batchdata_product_has_an_isolated_preview_and_execution(
    tmp_path, monkeypatch
):
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
        preview = service.quick_lists(payload)
        assert preview["status"] == "Preview"
        assert preview["call_plan"]["calls"][0]["product"] == "quick_lists"
        assert provider.calls == []
        executed = service.quick_lists(
            ProductRequest(
                selected_categories=["fsbo"],
                locations=["Chicago, IL"],
                confirmed=True,
                reason="manual product test",
            )
        )
        assert executed["status"] == "Completed"
        assert {call.product for call in db.query(ApiCall).all()} == {"quick_lists"}
        property_id = db.query(Property.id).first()[0]
        contact_preview = service.contact_enrichment(
            ContactEnrichmentRequest(property_ids=[property_id])
        )
        assert contact_preview["status"] == "Preview"
        assert contact_preview["call_plan"]["property_ids"] == [property_id]
    engine.dispose()
