import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "batchdata-test-key")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.batchdata.config import Configuration, ProductRequest
from app.batchdata.service import Service, initialize
from app.db import Base
from app.integration_data.models import CombinedProperty
from app.models import User
from app.routes.batchdata import records, refresh_distribution_pool
from tests.batchdata.test_service import FakeClient


def search(db, monkeypatch, mode, category):
    monkeypatch.setenv("BATCHDATA_API_MODE", mode)
    result = Service(db, FakeClient()).property_search(
        ProductRequest(
            selected_categories=[category],
            locations=["Chicago, IL"],
            confirmed=True,
            reason=f"{mode} test",
        )
    )
    assert result["status"] == "Completed"
    return result


def make_db(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path}/modes.db", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path / "archive"))
    db = sessionmaker(bind=engine, autoflush=False)()
    row = initialize(db)
    row.enabled = True
    row.configuration_json = Configuration(
        enabled=True, locations=["Chicago, IL"], selectedCategories=["fsbo"]
    ).dict()
    admin = User(
        email="admin@test.invalid", hashed_password="unused", role="platform_admin"
    )
    db.add(admin)
    db.commit()
    return db, admin


def test_live_mode_lists_only_live_records(tmp_path, monkeypatch):
    db, _ = make_db(tmp_path, monkeypatch)
    search(db, monkeypatch, "sandbox", "fsbo")
    live = search(db, monkeypatch, "live", "vacant")

    monkeypatch.setenv("BATCHDATA_API_MODE", "live")
    s = Service(db)
    properties = records("properties", 0, 100, s)
    assert {item["provider"] for item in properties["items"]} == {"batchdata"}
    assert properties["total"] == 2
    assert [item["id"] for item in records("runs", 0, 100, s)["items"]] == [live["id"]]
    assert {item["run_id"] for item in records("api-calls", 0, 100, s)["items"]} == {
        live["id"]
    }
    assert {item["run_id"] for item in records("saved-files", 0, 100, s)["items"]} == {
        live["id"]
    }
    assert records("memberships", 0, 100, s)["total"] == 2
    assert s.summary()["metrics"]["properties"] == 2
    assert s.summary()["metrics"]["runs"] == 1

    monkeypatch.setenv("BATCHDATA_API_MODE", "sandbox")
    sandbox = records("properties", 0, 100, Service(db))
    assert {item["provider"] for item in sandbox["items"]} == {"batchdata_sandbox"}
    db.close()


def test_completed_run_refreshes_only_its_mode_pool(tmp_path, monkeypatch):
    db, admin = make_db(tmp_path, monkeypatch)
    search(db, monkeypatch, "sandbox", "fsbo")
    live = search(db, monkeypatch, "live", "vacant")

    monkeypatch.setenv("BATCHDATA_API_MODE", "live")
    refresh_distribution_pool(Service(db), admin, live["id"])

    pool = db.query(CombinedProperty).all()
    assert {row.mode for row in pool} == {"live"}
    assert {
        source["source_id"] for row in pool for source in row.sources_json
    } == {"P2", "P3"}
    db.close()
