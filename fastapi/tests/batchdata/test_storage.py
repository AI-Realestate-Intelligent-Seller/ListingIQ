import io
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("SECRET_KEY", "batchdata-storage-test-key")

from app.batchdata.storage import (
    ArchiveCollisionError,
    LocalStorage,
    R2Storage,
    property_archive_key,
    read_with_fallback,
    run_archive_key,
    webhook_archive_key,
)


class MissingObject(Exception):
    response: ClassVar = {"Error": {"Code": "NoSuchKey"}}


class FakeR2Client:
    def __init__(self):
        self.objects = {}

    def put_object(self, **kwargs):
        location = (kwargs["Bucket"], kwargs["Key"])
        if kwargs.get("IfNoneMatch") == "*" and location in self.objects:
            error = RuntimeError("exists")
            error.response = {
                "Error": {"Code": "PreconditionFailed"},
                "ResponseMetadata": {"HTTPStatusCode": 412},
            }
            raise error
        self.objects[location] = kwargs["Body"]

    def get_object(self, **kwargs):
        try:
            content = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError as error:
            raise MissingObject() from error
        return {"Body": io.BytesIO(content)}


def test_run_keys_are_partitioned_by_mode_date_run_and_dataset():
    when = datetime(2026, 10, 1, 12, 30, tzinfo=timezone.utc)
    assert run_archive_key(
        "live", "run-1", "vacant-dallas", "response", occurred_at=when
    ) == (
        "mode=live/fetches/fetch-date=2026-10-01/run=run-1/"
        "dataset=vacant-dallas/response.json"
    )


def test_property_keys_are_partitioned_by_date_user_time_session_and_identity():
    when = datetime(2026, 10, 1, 12, 30, 45, tzinfo=timezone.utc)
    assert property_archive_key(
        42, "session-1", "pre foreclosure", "property/1", "property", occurred_at=when
    ) == (
        "fetch-date=2026-10-01/user=42/time=12-30-45/session=session-1/"
        "properties/category=pre-foreclosure/property=property-1/property.json"
    )
    assert webhook_archive_key(
        "live", "updated", "event-1", "delivery-1", "payload", when
    ) == (
        "mode=live/webhooks/received-date=2026-10-01/"
        "classification=updated/event=event-1/delivery=delivery-1/payload.json"
    )


def test_local_storage_is_append_only(tmp_path):
    storage = LocalStorage(tmp_path)
    key = "mode=live/fetches/fetch-date=2026-10-01/run=one/response.json"
    storage.write(key, b"first")
    assert storage.read(key) == b"first"
    with pytest.raises(ArchiveCollisionError):
        storage.write(key, b"replacement")


def test_r2_round_trip_is_append_only(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "private-archive")
    monkeypatch.setenv("BATCHDATA_R2_PREFIX", "batchdata")
    client = FakeR2Client()
    storage = R2Storage(client=client)
    key = "mode=live/fetches/fetch-date=2026-10-01/run=one/response.json"
    assert storage.write(key, b"payload") == key
    assert storage.read(key) == b"payload"
    assert ("private-archive", f"batchdata/{key}") in client.objects
    with pytest.raises(ArchiveCollisionError):
        storage.write(key, b"replacement")


def test_r2_reads_legacy_prefix_and_duplicated_bucket_key(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "private-archive")
    monkeypatch.setenv("BATCHDATA_R2_PREFIX", "")
    monkeypatch.setenv("BATCHDATA_R2_LEGACY_PREFIX", "batchdata")
    client = FakeR2Client()
    storage = R2Storage(client=client)
    key = "mode=live/fetches/fetch-date=2026-10-01/run=one/response.json"
    client.objects[("private-archive", f"private-archive/batchdata/{key}")] = b"old"
    assert storage.read(key) == b"old"


def test_r2_reads_fall_back_to_existing_local_archives(tmp_path, monkeypatch):
    old_key = "1/runs/old-run/properties/properties.json"
    local = LocalStorage(tmp_path)
    local.write(old_key, b"legacy")
    monkeypatch.setenv("BATCHDATA_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("BATCHDATA_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "private-archive")
    monkeypatch.setenv("CLOUDFLARE_R2_PREFIX", "batchdata")
    monkeypatch.setenv("CLOUDFLARE_R2_ENDPOINT", "https://r2.example.test")
    monkeypatch.setenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "test")
    monkeypatch.setattr(
        "app.batchdata.storage.R2Storage",
        lambda: R2Storage(client=FakeR2Client()),
    )
    assert read_with_fallback(old_key) == b"legacy"


def test_saved_file_schema_stays_pre_0026():
    from app.batchdata.models import SavedFile

    assert {column.name for column in SavedFile.__table__.columns} == {
        "id",
        "run_id",
        "kind",
        "relative_path",
        "size_bytes",
        "created_at",
    }


def test_nested_dataset_parts_remain_structured():
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    key = run_archive_key(
        "live",
        "run-1",
        "quick-lists/category=vacant/location=Dallas, TX/call=0",
        "response",
        occurred_at=when,
    )
    assert key == (
        "mode=live/fetches/fetch-date=2026-10-01/run=run-1/"
        "dataset=quick-lists/category=vacant/location=Dallas-TX/call=0/response.json"
    )


def test_webhook_archive_registers_payload_and_metadata_without_new_columns(
    tmp_path, monkeypatch
):
    from app.routes.batchdata import archive_webhook

    class FakeDb:
        def __init__(self):
            self.rows = []

        def add(self, row):
            self.rows.append(row)

    db = FakeDb()
    monkeypatch.setattr(
        "app.routes.batchdata.configured_storage", lambda: LocalStorage(tmp_path)
    )
    archive_webhook(
        db,
        {"propertyId": "provider-1"},
        "updated",
        "event-hash",
        "provider-1",
        "Update Received",
    )
    assert {row.kind for row in db.rows} == {
        "webhook_updated_payload",
        "webhook_updated_metadata",
    }
    assert all("classification=updated" in row.relative_path for row in db.rows)
    assert all((tmp_path / row.relative_path).is_file() for row in db.rows)
