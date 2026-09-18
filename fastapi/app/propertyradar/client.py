"""Only transport to PropertyRadar. Never log request headers or provider error bodies."""

import os
import time

import requests


class ProviderError(Exception):
    pass


class Client:
    def __init__(self):
        self.base = os.getenv(
            "PROPERTYRADAR_API_BASE_URL", "https://api.propertyradar.com"
        ).rstrip("/")
        self.token = os.getenv("PROPERTYRADAR_API_TOKEN", "")

    def request(self, method, path, params=None, body=None):
        if not self.token:
            raise ProviderError("PropertyRadar API token is not configured")
        # Only read-only calls and explicit previews can be retried.
        retryable = method == "GET" or (params or {}).get("Purchase") == 0
        for attempt in range(3 if retryable else 1):
            try:
                response = requests.request(
                    method,
                    self.base + path,
                    params=params,
                    json=body,
                    headers={
                        "X-Radar-Access-Token": self.token,
                        "Accept": "application/json",
                    },
                    timeout=(5, 30),
                    allow_redirects=False,
                )
                if (
                    response.status_code in (429, 502, 503, 504)
                    and retryable
                    and attempt < 2
                ):
                    time.sleep(0.5 * 2**attempt)
                    continue
                request_id = response.headers.get("X-Radar-Request-Id", "")[:100]
                if not 200 <= response.status_code < 300:
                    raise ProviderError(
                        f"PropertyRadar returned HTTP {response.status_code}; request {request_id}"
                    )
                data = response.json()
                if not isinstance(data, dict):
                    raise ProviderError("Unexpected PropertyRadar response format")
                return data, request_id
            except (requests.RequestException, ValueError):
                if retryable and attempt < 2:
                    time.sleep(0.5 * 2**attempt)
                    continue
                raise ProviderError(
                    "PropertyRadar transport failed; purchase outcome may require reconciliation"
                ) from None
        raise ProviderError("PropertyRadar request failed")

    def search_properties(self, params: dict, body: dict):
        return self.request("POST", "/v1/properties", params, body)

    def property_details(self, params: dict, body: dict):
        return self.request("POST", "/v1/properties", params, body)

    def property_persons(self, radar_id: str, params: dict):
        return self.request("GET", f"/v1/properties/{radar_id}/persons", params)

    def unlock_person_contact(self, person_key: str, kind: str, params: dict):
        return self.request("POST", f"/v1/persons/{person_key}/{kind}", params)
