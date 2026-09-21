"""Isolated integration tests; no AI/vector-store startup or external services."""

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "propertyradar-test-key")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db import Base
from app.propertyradar.service import Service, initialize
from app.routes.auth import get_db
from app.routes.propertyradar import index_router, router, webhook_router


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path}/test.db",
        connect_args={"check_same_thread": False, "timeout": 2},
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    with maker() as db:
        row = initialize(db)
        row.enabled = True
        row.configuration_json = {
            **row.configuration_json,
            "enabled": True,
            "state": "IL",
            "city": "Chicago",
            "selected_categories": ["expired", "vacant"],
            "billing_cycle_start": "2026-01-01",
        }
        db.commit()
    yield maker
    engine.dispose()


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture
def app(factory):
    app = FastAPI()
    app.include_router(index_router, prefix="/api/v1/platform-admin/integrations")
    app.include_router(router, prefix="/api/v1/integrations/propertyradar")
    app.include_router(webhook_router, prefix="/api/v1/webhooks")

    def dependency():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    return app


@pytest.fixture
def client(app, monkeypatch):
    monkeypatch.setenv("PROPERTYRADAR_WEBHOOK_SECRET", "s" * 40)
    return TestClient(app)


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.cost = "0.00"
        self.free = 50000
        self.population = {"1": ["P1", "P2"], "2": ["P2", "P3"]}
        self.contact_count = 1
        self.fail_purchase = False

    def request(self, method, path, params=None, body=None):
        from app.propertyradar.client import ProviderError

        params = params or {}
        self.calls.append((method, path, dict(params), body))
        if params.get("Purchase") == 1 and self.fail_purchase:
            raise ProviderError("Unknown transport outcome")
        data = {
            "totalCost": self.cost,
            "quantityFreeRemaining": self.free,
            "resultCount": 1,
        }
        if path == "/v1/properties":
            criteria = body["Criteria"]
            ids = (
                criteria[0]["value"]
                if criteria[0]["name"] == "RadarID"
                else ["P1", "P2"]
            )
            if criteria[-1]["name"] == "isSiteVacant":
                ids = ["P2", "P3"]
            data.update(
                resultCount=len(ids),
                results=[
                    {
                        "RadarID": radar,
                        "Persons": [
                            {
                                "PersonKey": "owner",
                                "OwnershipRole": "Owner",
                                "isPrimaryContact": 1,
                            }
                        ],
                    }
                    for radar in ids
                ],
            )
        elif path.startswith("/v1/lists/"):
            key = path.split("/")[3]
            ids = self.population[key]
            if path.endswith("/items"):
                start = params["Start"]
                data["results"] = [{"RadarID": v} for v in ids[start : start + 1000]]
            else:
                data["results"] = [
                    {"ListID": key, "TotalCount": len(ids), "isMonitored": 0}
                ]
        elif path.endswith("/persons"):
            data["results"] = [
                {
                    "PersonKey": "owner",
                    "OwnershipRole": "Owner",
                    "isPrimaryContact": 1,
                    "Phone": [
                        {"href": "tel:+1-555-555-1234", "status": "Active"},
                        {"linktext": "555-555-9999", "status": "DoNotCall"},
                    ],
                    "Email": [{"href": "mailto:Owner@Example.com"}],
                }
            ]
        elif path.endswith(("/Phone", "/Email")):
            data["resultCount"] = self.contact_count
        return data, "test-request"

    def search_properties(self, params, body):
        return self.request("POST", "/v1/properties", params, body)

    def property_details(self, params, body):
        return self.request("POST", "/v1/properties", params, body)

    def property_persons(self, radar_id, params):
        return self.request("GET", f"/v1/properties/{radar_id}/persons", params)

    def unlock_person_contact(self, person_key, kind, params):
        return self.request("POST", f"/v1/persons/{person_key}/{kind}", params)


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def service(db, provider):
    return Service(db, provider)
