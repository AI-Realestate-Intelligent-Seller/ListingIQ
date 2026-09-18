"""Dedicated DealMachine transport. Authorization is never returned or logged."""

import random
import time
import uuid
from dataclasses import dataclass

import requests


class ProviderError(RuntimeError):
    def __init__(self, message, status_code=None, request_id=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = retryable


@dataclass
class ProviderResponse:
    data: dict
    request_id: str
    headers: dict
    content: bytes | None = None
    content_type: str = "application/json"


class Client:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def request(self, method: str, path: str, body=None, timeout=45, retryable=None):
        if not self.api_key:
            raise ProviderError("DealMachine API key is not configured", 401)
        # Paid mutations are deliberately never retried. Reads, metadata, count,
        # estimates, list status and activity are safe to retry.
        if retryable is None:
            retryable = (
                method == "GET"
                or path.endswith("/count")
                or bool((body or {}).get("estimate_cost"))
            )
        maximum = 3 if retryable else 1
        for attempt in range(maximum):
            try:
                response = requests.request(
                    method,
                    self.base_url + path,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json=body if method != "GET" or body else None,
                    timeout=(5, timeout),
                    allow_redirects=False,
                )
            except requests.RequestException as error:
                if retryable and attempt + 1 < maximum:
                    time.sleep((0.35 * 2**attempt) + random.uniform(0, 0.2))
                    continue
                raise ProviderError(
                    "DealMachine transport failed", retryable=retryable
                ) from error
            request_id = response.headers.get("x-request-id", uuid.uuid4().hex)[:160]
            audit_headers = {
                key.lower(): value[:1000]
                for key, value in response.headers.items()
                if key.lower()
                in {
                    "x-request-id",
                    "retry-after",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset",
                    "content-type",
                }
            }
            if response.status_code == 429 and retryable and attempt + 1 < maximum:
                try:
                    delay = min(float(response.headers.get("Retry-After", "1")), 30)
                except ValueError:
                    delay = 1
                time.sleep(delay + random.uniform(0, 0.2))
                continue
            if not 200 <= response.status_code < 300:
                code = "provider_error"
                try:
                    payload = response.json()
                    code = payload.get("error", {}).get("code", code)
                except ValueError:
                    pass
                raise ProviderError(
                    f"DealMachine returned HTTP {response.status_code} ({code}); request {request_id}",
                    response.status_code,
                    request_id,
                    response.status_code in {429, 502, 503, 504},
                )
            content_type = response.headers.get("content-type", "application/json")
            if "json" not in content_type.lower():
                return ProviderResponse(
                    {}, request_id, audit_headers, response.content, content_type
                )
            try:
                data = response.json()
            except ValueError as error:
                raise ProviderError(
                    "DealMachine returned invalid JSON", request_id=request_id
                ) from error
            if not isinstance(data, dict):
                raise ProviderError(
                    "DealMachine returned an unexpected response", request_id=request_id
                )
            return ProviderResponse(data, request_id, audit_headers)
        raise ProviderError("DealMachine request failed")

    def account(self):
        return self.request("GET", "/account")

    def filters(self):
        return self.request("GET", "/filters?source_type=properties")

    def fields(self):
        return self.request("GET", "/fields?source_type=properties")

    def usage(self):
        return self.request("GET", "/usage")

    def property_count(self, body):
        return self.request("POST", "/properties/search/count", body, retryable=True)

    def property_estimate(self, body):
        return self.request("POST", "/properties/search", body, retryable=True)

    def property_search(self, body):
        return self.request("POST", "/properties/search", body, retryable=False)

    def property_details(self, body):
        return self.request("POST", "/properties/ids", body, retryable=False)

    def contact_enrichment(self, body):
        return self.request("POST", "/properties/ids", body, retryable=False)

    def create_list(self, body):
        return self.request("POST", "/lists", body, retryable=False)

    def list_status(self, list_id):
        return self.request("GET", f"/lists/{list_id}")

    def add_list_items(self, list_id, body):
        return self.request("POST", f"/lists/{list_id}/items", body, retryable=False)

    def remove_list_items(self, list_id, body):
        return self.request("DELETE", f"/lists/{list_id}/items", body, retryable=False)

    def activity_search(self, body):
        return self.request("POST", "/activity/search", body, retryable=True)

    def activity_detail(self, activity_id):
        return self.request("GET", f"/activity/{activity_id}")

    def property_export(self, body):
        return self.request(
            "POST", "/properties/export", body, timeout=150, retryable=False
        )
