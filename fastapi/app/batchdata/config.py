import ipaddress
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, validator

CATEGORIES = {
    "fsbo": "FSBO",
    "pre_foreclosure": "Pre-Foreclosure",
    "expired": "Expired",
    "pending": "Pending",
    "canceled": "Canceled",
    "active_auction": "Active Auction",
    "tax_default": "Tax Default",
    "vacant": "Vacant",
    "absentee_owner": "Absentee Owner",
    "high_equity": "High Equity",
    "cash_buyer": "Cash Buyer",
    "vacant_lot": "Vacant Lot",
    "notice_of_default": "Notice of Default",
    "lis_pendens": "Lis Pendens",
    "withdrawn": "Withdrawn",
    "probate": "Probate",
    "divorce": "Divorce",
    "bankruptcy": "Bankruptcy",
    "reo": "REO",
    "foreclosure": "Foreclosure",
    "short_sale": "Short Sale",
}

UNSUPPORTED = {"short_sale"}
LISTING_CATEGORIES = {"active", "pending", "expired", "canceled", "withdrawn"}
FORECLOSURE_CATEGORIES = {
    "pre_foreclosure",
    "foreclosure",
    "active_auction",
    "notice_of_default",
    "lis_pendens",
}


def public_https(value: str) -> bool:
    parsed = urlparse(value)
    host = parsed.hostname or ""
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        return False
    if (
        host == "localhost"
        or host.endswith((".localhost", ".local"))
        or "." not in host
    ):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


class Configuration(BaseModel):
    configurationVersion: int = Field(1, ge=1)
    enabled: bool = False
    billingMode: Literal["pay_as_you_go"] = "pay_as_you_go"
    monthlySpendCap: float = Field(250, ge=0, le=1_000_000)
    skipTraceSpendCap: float = Field(175, ge=0, le=1_000_000)
    monthlySkipTraceLimit: int = Field(2500, ge=0, le=1_000_000)
    basicPropertyUnitCost: float = Field(0.01, ge=0, le=1000)
    quickListUnitCost: float = Field(0.01, ge=0, le=1000)
    listingUnitCost: float = Field(0.10, ge=0, le=1000)
    preForeclosureUnitCost: float = Field(0.06, ge=0, le=1000)
    contactEnrichmentUnitCost: float = Field(0.07, ge=0, le=1000)
    allowOverage: Literal[False] = False
    rowsPerCategory: int = Field(20, ge=1, le=20)
    selectedCategories: list[str] = Field(
        default_factory=lambda: [key for key in CATEGORIES if key not in UNSUPPORTED],
        max_items=21,
    )
    locations: list[str] = Field(default_factory=list, max_items=100)
    combination: Literal["AND", "OR"] = "OR"
    quickListsEnabled: bool = True
    basicPropertyEnabled: bool = True
    listingEnabled: bool = False
    preForeclosureEnabled: bool = False
    contactEnrichmentEnabled: bool = True
    publicWebhookUrl: str = Field("", max_length=2000)
    monitorNewMatchUnitCost: float | None = Field(None, ge=0, le=1000)
    monitorUpdateUnitCost: float | None = Field(None, ge=0, le=1000)
    monitorMonthlyFixedCost: float | None = Field(None, ge=0, le=1_000_000)
    monitorMonthlySpendCap: float | None = Field(None, ge=0, le=1_000_000)

    class Config:
        extra = "forbid"

    @validator("selectedCategories")
    def known_categories(cls, value):
        value = list(dict.fromkeys(value))
        unknown = set(value) - set(CATEGORIES)
        if unknown:
            raise ValueError(f"Unknown categories: {', '.join(sorted(unknown))}")
        if set(value) & UNSUPPORTED:
            raise ValueError("Short Sale is unsupported and must remain disabled")
        return value

    @validator("locations")
    def clean_locations(cls, value):
        cleaned = [item.strip() for item in value if item.strip()]
        return list(dict.fromkeys(cleaned))

    @validator("publicWebhookUrl")
    def valid_webhook(cls, value):
        if value and not public_https(value):
            raise ValueError(
                "A public HTTPS tunnel or production URL is required for BatchData webhooks"
            )
        return value


class ProductRequest(BaseModel):
    selected_categories: list[str] = Field(min_items=1, max_items=21)
    locations: list[str] = Field(min_items=1, max_items=100)
    combination: Literal["AND", "OR"] = "OR"
    rows_per_category: int = Field(20, ge=1, le=20)
    confirmed: bool = False
    reason: str = Field("", max_length=500)

    class Config:
        extra = "forbid"

    @validator("selected_categories")
    def valid_categories(cls, value):
        clean = list(dict.fromkeys(value))
        unknown = set(clean) - set(CATEGORIES)
        if unknown:
            raise ValueError(f"Unknown categories: {', '.join(sorted(unknown))}")
        if set(clean) & UNSUPPORTED:
            raise ValueError("Short Sale is unsupported")
        return clean

    @validator("locations")
    def valid_locations(cls, value):
        clean = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not clean:
            raise ValueError("At least one location is required")
        return clean


class ContactEnrichmentRequest(BaseModel):
    property_ids: list[int] = Field(min_items=1, max_items=10000)
    confirmed: bool = False
    reason: str = Field("", max_length=500)
    preview_hash: str = Field("", max_length=64)

    class Config:
        extra = "forbid"

    @validator("property_ids")
    def unique_property_ids(cls, value):
        return list(dict.fromkeys(value))


class ProductRunResponse(BaseModel):
    id: str
    status: str
    configuration_version: int
    call_plan: dict
    estimated_cost: float
    actual_cost: float
    returned_records: int
    unique_properties: int
    duplicate_properties: int
    provider_calls: list[dict] = Field(default_factory=list)
    properties: list[dict] = Field(default_factory=list)

    class Config:
        orm_mode = True
