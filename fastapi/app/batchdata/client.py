import os
import uuid

import requests


class ProviderError(RuntimeError):
    pass


class Client:
    def __init__(self):
        self.base_url = os.getenv(
            "BATCHDATA_API_URL", "https://api.batchdata.com"
        ).rstrip("/")
        self.token = os.getenv("BATCHDATA_API_TOKEN", "")

    def request(self, method: str, path: str, body: dict):
        if not self.token:
            raise ProviderError("BatchData API token is not configured")
        try:
            response = requests.request(
                method,
                self.base_url + path,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=(5, 45),
            )
        except requests.RequestException as error:
            raise ProviderError(
                "BatchData transport failed; reconcile before retrying"
            ) from error
        request_id = response.headers.get("x-request-id", uuid.uuid4().hex)
        if response.status_code >= 400:
            raise ProviderError(
                f"BatchData returned HTTP {response.status_code}; request {request_id}"
            )
        try:
            value = response.json()
        except ValueError as error:
            raise ProviderError(
                "BatchData returned an invalid JSON response"
            ) from error
        if not isinstance(value, dict):
            raise ProviderError("BatchData returned an unexpected response format")
        return value, request_id

    def quick_lists(self, body: dict):
        return self._property_product(body, "quick_lists")

    def basic_property(self, body: dict):
        return self.request("POST", "/api/v1/property/lookup/all-attributes", body)

    def listing_data(self, body: dict):
        return self.basic_property(body)

    def pre_foreclosure(self, body: dict):
        return self.basic_property(body)

    def contact_enrichment(self, body: dict):
        return self.request("POST", "/api/v3/property/skip-trace", body)

    def _property_product(self, body: dict, product: str):
        # Product names are internal permissions, not BatchData dataset selectors.
        request = {key: value for key, value in body.items() if key != "dataTypes"}
        return self.request("POST", "/api/v1/property/search", request)
