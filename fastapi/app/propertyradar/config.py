import calendar
import ipaddress
import os
from datetime import date, datetime, timezone
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, validator

from .categories import CATEGORIES

PUBLIC_URL_MESSAGE = "A public HTTPS URL is required for PropertyRadar to reach this webhook. Use a development tunnel such as ngrok or Cloudflare Tunnel, or configure the production URL."


def public_https(value):
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


def local_mode():
    return urlparse(os.getenv("FRONTEND_URL", "http://localhost:3000")).hostname in (
        "localhost",
        "127.0.0.1",
        "::1",
    )


class Configuration(BaseModel):
    enabled: bool = False
    public_webhook_url: str = Field("", max_length=2000)
    state: str = Field("", max_length=2)
    city: str = Field("", max_length=100)
    zip_codes: list[str] = Field(default_factory=list, max_items=100)
    county_fips: str = Field("", max_length=5)
    initial_rows: int = Field(20, ge=1, le=100)
    high_equity_min: int = Field(50, ge=0, le=100)
    selected_categories: list[str] = Field(default_factory=list, max_items=21)
    monitor_new_matches: bool = True
    monitor_status_changes: bool = True
    skiptrace_new: bool = True
    skiptrace_initial: bool = False
    contact_mode: Literal["primary", "all"] = "primary"
    phone_limit: int = Field(2450, ge=0, le=2450)
    email_limit: int = Field(2450, ge=0, le=2450)
    export_limit: int = Field(45000, ge=0, le=45000)
    monitored_limit: int = Field(45000, ge=0, le=45000)
    billing_cycle_start: date | None = None

    class Config:
        extra = "forbid"

    @validator("selected_categories")
    def categories_known(cls, value):
        if any(key not in CATEGORIES for key in value):
            raise ValueError("Unknown category")
        return list(dict.fromkeys(value))

    @validator("public_webhook_url")
    def url_valid(cls, value):
        if value and not public_https(value):
            raise ValueError(PUBLIC_URL_MESSAGE)
        return value

    @validator("state")
    def state_valid(cls, value):
        value = value.strip().upper()
        if value and (len(value) != 2 or not value.isalpha()):
            raise ValueError("Use a two-letter state code")
        return value

    @validator("zip_codes")
    def zips_valid(cls, values):
        if any(len(v) != 5 or not v.isdigit() for v in values):
            raise ValueError("ZIP codes must contain five digits")
        return list(dict.fromkeys(values))

    @validator("county_fips")
    def fips_valid(cls, value):
        if value and (len(value) not in (4, 5) or not value.isdigit()):
            raise ValueError("County FIPS must contain four or five digits")
        return value


class PropertySearchRequest(BaseModel):
    category_ids: list[str] = Field(min_items=1, max_items=21)
    state: str = Field("", max_length=2)
    city: str = Field("", max_length=100)
    zip_codes: list[str] = Field(default_factory=list, max_items=100)
    county_fips: str = Field("", max_length=5)
    rows_per_category: int = Field(20, ge=1, le=100)

    class Config:
        extra = "forbid"

    @validator("category_ids")
    def search_categories_known(cls, value):
        if any(key not in CATEGORIES for key in value):
            raise ValueError("Unknown category")
        return list(dict.fromkeys(value))

    @validator("state")
    def search_state_valid(cls, value):
        return Configuration.state_valid(value)

    @validator("zip_codes")
    def search_zips_valid(cls, value):
        return Configuration.zips_valid(value)

    @validator("county_fips")
    def search_fips_valid(cls, value):
        return Configuration.fips_valid(value)


class PropertyDetailsRequest(BaseModel):
    radar_ids: list[str] = Field(min_items=1, max_items=500)
    category_ids: list[str] = Field(default_factory=list, max_items=21)
    confirmed: bool = False
    reason: str = Field("", max_length=500)

    class Config:
        extra = "forbid"

    @validator("radar_ids")
    def clean_radar_ids(cls, value):
        clean = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not clean or any(len(item) > 100 for item in clean):
            raise ValueError("Use 1-500 valid RadarIDs")
        return clean

    @validator("category_ids")
    def detail_categories_known(cls, value):
        if any(key not in CATEGORIES for key in value):
            raise ValueError("Unknown category")
        return list(dict.fromkeys(value))


class ContactEnrichmentRequest(BaseModel):
    radar_ids: list[str] = Field(min_items=1, max_items=100)
    confirmed: bool = False
    reason: str = Field("", max_length=500)

    class Config:
        extra = "forbid"

    @validator("radar_ids")
    def clean_radar_ids(cls, value):
        clean = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not clean or any(len(item) > 100 for item in clean):
            raise ValueError("Use 1-100 valid RadarIDs")
        return clean


class PropertySearchResponse(BaseModel):
    raw_radar_ids: dict[str, list[str]]
    memberships: dict[str, list[str]]
    total_before_deduplication: int
    unique_count: int
    duplicate_occurrences: int


class PropertyDetailsResponse(BaseModel):
    requested: int
    created: int
    existing: int


class ContactEnrichmentResponse(BaseModel):
    requested: int
    completed: int
    already_completed: int


def cycle(config, today=None):
    today = today or datetime.now(timezone.utc).date()
    anchor = config.get("billing_cycle_start")
    if not anchor:
        raise ValueError("Set the provider billing-cycle start date before purchases")
    anchor = date.fromisoformat(anchor)
    if anchor > today:
        raise ValueError("Billing-cycle start date cannot be in the future")
    day = anchor.day
    start = today.replace(day=min(day, calendar.monthrange(today.year, today.month)[1]))
    if start > today:
        month = today.month - 1 or 12
        year = today.year - (today.month == 1)
        start = date(year, month, min(day, calendar.monthrange(year, month)[1]))
    month = start.month % 12 + 1
    year = start.year + (start.month == 12)
    end = date(year, month, min(day, calendar.monthrange(year, month)[1]))
    return start.isoformat(), end.isoformat()
