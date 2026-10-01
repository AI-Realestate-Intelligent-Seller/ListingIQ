import pytest


@pytest.fixture(autouse=True)
def local_batchdata_archive(monkeypatch):
    # Never let focused tests inherit a developer machine's live R2 setting.
    monkeypatch.setenv("BATCHDATA_STORAGE_BACKEND", "local")
