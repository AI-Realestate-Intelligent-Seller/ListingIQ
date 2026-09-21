from app.batchdata.client import Client


def test_product_methods_omit_unsupported_internal_data_types(monkeypatch):
    client = Client()
    calls = []

    def request(method, path, body):
        calls.append((method, path, body))
        return {"results": []}, "request-id"

    monkeypatch.setattr(client, "request", request)
    payload = {
        "searchCriteria": {"query": "Chicago, IL"},
        "options": {"take": 20, "skip": 0},
    }
    client.quick_lists({**payload, "dataTypes": ["untrusted"]})
    assert calls[-1] == (
        "POST",
        "/api/v1/property/search",
        payload,
    )
    lookup = {
        "requests": [
            {"address": {"street": "123 Main St", "city": "Chicago", "state": "IL"}}
        ]
    }
    for method in (client.basic_property, client.listing_data, client.pre_foreclosure):
        method(lookup)
        assert calls[-1] == ("POST", "/api/v1/property/lookup/all-attributes", lookup)


def test_contact_enrichment_has_its_own_provider_method(monkeypatch):
    client = Client()
    calls = []
    monkeypatch.setattr(
        client,
        "request",
        lambda method, path, body: (
            calls.append((method, path, body)) or ({"results": []}, "request-id")
        ),
    )
    payload = {"properties": [{"_id": "P1"}]}
    client.contact_enrichment(payload)
    assert calls == [("POST", "/api/v3/property/skip-trace", payload)]
