"""Persona R2 can use its own bucket and key pair without touching BatchData's."""

import os

import pytest

from app.personas import storage as persona_storage

NAMES = ("CLOUDFLARE_R2_BUCKET", "CLOUDFLARE_R2_ENDPOINT", "CLOUDFLARE_R2_ACCESS_KEY_ID",
         "CLOUDFLARE_R2_SECRET_ACCESS_KEY", "PERSONA_R2_BUCKET", "PERSONA_R2_ENDPOINT", "PERSONA_R2_PREFIX",
         "PERSONA_R2_CREDENTIALS_FILE", "PERSONA_R2_ACCESS_KEY_ID", "PERSONA_R2_SECRET_ACCESS_KEY")


@pytest.fixture
def shared(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "batchdata-bucket")
    monkeypatch.setenv("CLOUDFLARE_R2_ENDPOINT", "https://acct.r2.example/batchdata-bucket")
    monkeypatch.setenv("CLOUDFLARE_R2_ACCESS_KEY_ID", "batch-id")
    monkeypatch.setenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", "batch-secret")
    return monkeypatch


def _captured(monkeypatch):
    seen = {}
    boto3 = pytest.importorskip("boto3")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: seen.update(k) or object())
    return seen


def test_shared_values_remain_the_default(shared):
    seen = _captured(shared)
    store = persona_storage.R2PersonaStorage()
    assert store.bucket == "batchdata-bucket"
    assert seen["aws_access_key_id"] == "batch-id"


def test_persona_values_and_private_file_override_without_exporting(shared, tmp_path):
    creds = tmp_path / "r2.env"
    creds.write_text("CLOUDFLARE_R2_ACCESS_KEY_ID=test-id\nCLOUDFLARE_R2_SECRET_ACCESS_KEY=test-secret\n")
    creds.chmod(0o600)
    shared.setenv("PERSONA_R2_BUCKET", "persona-test")
    shared.setenv("PERSONA_R2_ENDPOINT", "https://acct.r2.example")
    shared.setenv("PERSONA_R2_CREDENTIALS_FILE", str(creds))
    seen = _captured(shared)
    store = persona_storage.R2PersonaStorage()
    assert store.bucket == "persona-test"
    assert seen["endpoint_url"] == "https://acct.r2.example"
    assert (seen["aws_access_key_id"], seen["aws_secret_access_key"]) == ("test-id", "test-secret")
    # BatchData's shared pair is unchanged in the process environment.
    assert os.environ["CLOUDFLARE_R2_ACCESS_KEY_ID"] == "batch-id"
    assert os.environ["CLOUDFLARE_R2_BUCKET"] == "batchdata-bucket"


def test_credentials_file_never_falls_back_to_shared_pair(shared, tmp_path):
    shared.setenv("PERSONA_R2_CREDENTIALS_FILE", str(tmp_path / "missing.env"))
    with pytest.raises(persona_storage.PersonaStorageError):
        persona_storage.R2PersonaStorage()
    loose = tmp_path / "loose.env"
    loose.write_text("CLOUDFLARE_R2_ACCESS_KEY_ID=x\nCLOUDFLARE_R2_SECRET_ACCESS_KEY=y\n")
    loose.chmod(0o644)
    shared.setenv("PERSONA_R2_CREDENTIALS_FILE", str(loose))
    with pytest.raises(persona_storage.PersonaStorageError):
        persona_storage.R2PersonaStorage()
