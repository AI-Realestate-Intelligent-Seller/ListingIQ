from typing import Literal

from pydantic import BaseModel, Field, validator

CATEGORIES = [
    ("fsbo", "FSBO"),
    ("expired", "Expired"),
    ("pending", "Pending"),
    ("withdrawn", "Withdrawn"),
    ("cancelled", "Cancelled"),
    ("foreclosure", "Foreclosure"),
    ("pre_foreclosure", "Pre-Foreclosure"),
    ("auction", "Auction"),
    ("tax_default", "Tax Default/Tax Delinquent"),
    ("bankruptcy", "Bankruptcy"),
    ("nod", "NOD"),
    ("lis_pendens", "Lis Pendens"),
    ("short_sale", "Short Sale"),
    ("absentee_owner", "Absentee Owner"),
    ("probate", "Probate"),
    ("divorce", "Divorce"),
    ("reo", "REO/Bank-Owned"),
    ("high_equity", "High Equity"),
    ("cash_buyer", "Cash Buyer"),
    ("vacant_property", "Vacant Property"),
    ("vacant_land", "Vacant Land"),
]


class Limits(BaseModel):
    monthly_total_credit_cap: int = Field(5000, ge=0, le=10_000_000)
    monthly_property_credit_cap: int = Field(3500, ge=0, le=10_000_000)
    monthly_people_credit_cap: int = Field(1500, ge=0, le=10_000_000)
    per_run_property_limit: int = Field(500, ge=1, le=100_000)
    per_run_people_limit: int = Field(250, ge=0, le=100_000)
    rows_per_category: int = Field(20, ge=1, le=250)
    maximum_categories_per_run: int = Field(21, ge=1, le=21)
    daily_request_limit: int = Field(4500, ge=1, le=5000)
    minimum_remaining_credit_reserve: int = Field(100, ge=0, le=10_000_000)
    stop_on_warning: bool = True


class Settings(BaseModel):
    api_key: str | None = Field(None, min_length=12, max_length=500)
    limits: Limits = Field(default_factory=Limits)
    selected_fields: list[str] = Field(default_factory=list, max_items=250)

    class Config:
        extra = "forbid"


class Location(BaseModel):
    type: Literal["state", "county", "city", "zip_code", "radius", "polygon"]
    code: str | None = Field(None, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    radius_miles: float | None = Field(None, gt=0, le=1000)
    coordinates: list[list[float]] | None = None

    @validator("code")
    def clean_code(cls, value):
        return value.strip() if value else value


class FilterValue(BaseModel):
    filter_id: str = Field(min_length=1, max_length=160, regex=r"^[A-Za-z0-9_.-]+$")
    operator: str | None = Field(None, max_length=80)
    value: object


class SearchRequest(BaseModel):
    category_ids: list[str] = Field(default_factory=list, max_items=21)
    filters: list[FilterValue] = Field(default_factory=list, max_items=100)
    locations: list[Location] = Field(default_factory=list, max_items=15)
    fields: list[str] = Field(default_factory=list, max_items=250)
    sort: list[dict] = Field(default_factory=list, max_items=10)
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=250)
    confirmed: bool = False
    reason: str = Field("", max_length=500)
    idempotency_key: str | None = Field(None, max_length=160)


class DetailsRequest(BaseModel):
    dm_property_ids: list[str] = Field(min_items=1, max_items=250)
    fields: list[str] = Field(default_factory=list, max_items=250)
    confirmed: bool = False
    reason: str = Field("", max_length=500)

    @validator("dm_property_ids")
    def unique_ids(cls, values):
        clean = list(
            dict.fromkeys(str(value).strip() for value in values if str(value).strip())
        )
        if not clean:
            raise ValueError("At least one property ID is required")
        return clean


class ContactEnrichmentRequest(BaseModel):
    dm_property_ids: list[str] = Field(min_items=1, max_items=250)
    contact_audience: Literal["owners", "owners_and_family", "renters", "residents"] = (
        "owners"
    )
    fields: list[str] = Field(default_factory=list, max_items=250)
    confirmed: bool = False
    force: bool = False
    reason: str = Field("", max_length=500)

    @validator("dm_property_ids")
    def unique_ids(cls, values):
        clean = list(
            dict.fromkeys(str(value).strip() for value in values if str(value).strip())
        )
        if not clean:
            raise ValueError("At least one property ID is required")
        return clean


class ListRequest(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    category_ids: list[str] = Field(default_factory=list, max_items=21)
    filters: list[FilterValue] = Field(default_factory=list, max_items=100)
    locations: list[Location] = Field(default_factory=list, max_items=15)
    record_ids: list[str] = Field(default_factory=list, max_items=250)


class ListItemsRequest(BaseModel):
    ids: list[str] = Field(min_items=1, max_items=10_000)
    id_type: Literal["internal_property_id"] = "internal_property_id"
    confirmed: bool = False
    reason: str = Field("", max_length=500)


class ActivitySearchRequest(BaseModel):
    query: str = Field("", max_length=300)
    filters: dict = Field(default_factory=dict)
    date_range: dict = Field(default_factory=dict)
    entity_ids: list[str] = Field(default_factory=list, max_items=1000)
    sort: list[dict] = Field(default_factory=list, max_items=10)
    page: int = Field(1, ge=1)
    per_page: int = Field(25, ge=1, le=250)


class ExportRequest(SearchRequest):
    list_id: str | None = Field(None, max_length=160)
    expected_count: int = Field(ge=0)
    estimated_credits: int = Field(ge=0)


class CategoryConfig(BaseModel):
    enabled: bool = False
    support_status: Literal["available", "unavailable", "unconfirmed"] = "unconfirmed"
    filter_mappings: list[FilterValue] = Field(default_factory=list, max_items=100)
    locations: list[Location] = Field(default_factory=list, max_items=15)
    selected_fields: list[str] = Field(default_factory=list, max_items=250)
    rows_per_fetch: int = Field(20, ge=1, le=250)
    sort: list[dict] = Field(default_factory=list, max_items=10)
