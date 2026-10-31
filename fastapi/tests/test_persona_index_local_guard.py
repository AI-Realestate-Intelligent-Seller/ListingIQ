"""PERSONA_INDEX_REQUIRE_LOCAL_QDRANT keeps the indexing worker off remote Qdrant."""

from types import SimpleNamespace

import pytest

from app.personas import knowledge_indexing as ki


@pytest.mark.parametrize("url, blocked", [
    ("http://127.0.0.1:6335", False),
    ("http://localhost:6333", False),
    ("https://abc.eu-central-1-0.aws.cloud.qdrant.io:6333", True),
    ("", True),
])
def test_guard_allows_only_loopback_qdrant(monkeypatch, url, blocked):
    monkeypatch.setenv("PERSONA_INDEX_REQUIRE_LOCAL_QDRANT", "true")
    assert ki.local_qdrant_required_but_missing(SimpleNamespace(backend="qdrant", url=url)) is blocked


def test_guard_rejects_non_qdrant_backend_and_is_off_by_default(monkeypatch):
    store = SimpleNamespace(backend="chroma", url=None)
    monkeypatch.delenv("PERSONA_INDEX_REQUIRE_LOCAL_QDRANT", raising=False)
    assert ki.local_qdrant_required_but_missing(store) is False
    monkeypatch.setenv("PERSONA_INDEX_REQUIRE_LOCAL_QDRANT", "true")
    assert ki.local_qdrant_required_but_missing(store) is True


def test_worker_does_not_start_against_remote_store(monkeypatch):
    monkeypatch.setenv("PERSONA_INDEX_WORKER_ENABLED", "true")
    monkeypatch.setenv("PERSONA_INDEX_REQUIRE_LOCAL_QDRANT", "true")
    monkeypatch.setattr(ki, "vector_store", SimpleNamespace(backend="qdrant", url="https://x.cloud.qdrant.io"))
    assert ki.start_worker() is None
