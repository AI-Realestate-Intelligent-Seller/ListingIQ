import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.dealmachine.client import Client, ProviderError


class Response:
    def __init__(self, status, payload, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = b""

    def json(self):
        return self._payload


def test_retry_after_for_free_request(monkeypatch):
    responses = iter(
        [
            Response(429, {"error": {"code": "rate_limited"}}, {"Retry-After": "0"}),
            Response(
                200,
                {"data": []},
                {"x-request-id": "ok", "content-type": "application/json"},
            ),
        ]
    )
    calls = []
    monkeypatch.setattr(
        "app.dealmachine.client.requests.request",
        lambda *args, **kwargs: calls.append((args, kwargs)) or next(responses),
    )
    monkeypatch.setattr("app.dealmachine.client.time.sleep", lambda _: None)
    result = Client("https://api.v2.dealmachine.com/v1", "secret").filters()
    assert result.request_id == "ok" and len(calls) == 2
    assert calls[0][1]["headers"]["Authorization"] == "Bearer secret"


def test_paid_request_is_never_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.dealmachine.client.requests.request",
        lambda *args, **kwargs: (
            calls.append(1)
            or Response(
                503, {"error": {"code": "unavailable"}}, {"x-request-id": "failed"}
            )
        ),
    )
    with pytest.raises(ProviderError):
        Client("https://api.v2.dealmachine.com/v1", "secret").property_search(
            {"per_page": 20}
        )
    assert len(calls) == 1
