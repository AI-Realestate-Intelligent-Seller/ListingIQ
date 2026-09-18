"""Verified against https://developers.propertyradar.com/criteria_reference (2026-09-09)."""

from copy import deepcopy

_DEFINITIONS = [
    ("fsbo", "FSBO", "isListedForSaleByOwner", [1]),
    ("pre_foreclosure", "Pre-Foreclosure", "ForeclosureStage", ["Preforeclosure"]),
    ("expired", "Expired", "ListingStatus", ["Expired"]),
    ("pending", "Pending", "ListingStatus", ["Pending"]),
    ("withdrawn", "Withdrawn", "ListingStatus", ["Withdrawn"]),
    ("cancelled", "Cancelled", "ListingStatus", ["Cancelled"]),
    ("foreclosure", "Foreclosure", "inForeclosure", [1]),
    ("auction", "Auction", "ForeclosureStage", ["Auction"]),
    ("reo", "REO / Bank-Owned", "ForeclosureStage", ["Bank Owned"]),
    ("short_sale", "Short Sale", "ListingType", ["ShortSale"]),
    ("probate", "Probate", "inProbateProperty", [1]),
    ("divorce", "Divorce", "inDivorce", [1]),
    ("tax_delinquent", "Tax Delinquent / Tax Default", "inTaxDelinquency", [1]),
    ("vacant", "Vacant Property", "isSiteVacant", [1]),
    ("absentee", "Absentee Owner", "isSameMailingOrExempt", [0]),
    ("high_equity", "High Equity", "EquityPercent", [[50, None]]),
    ("cash_buyer", "Cash Buyer", "isCashTransaction", [1]),
    (
        "vacant_land",
        "Vacant Land",
        "PropertyType",
        [{"name": "PType", "value": ["LND"]}],
    ),
    ("bankruptcy", "Bankruptcy", "inBankruptcy", [1]),
    ("notice_of_default", "Notice of Default", "ForeclosureDocType", ["NDF"]),
    ("lis_pendens", "Lis Pendens", "ForeclosureDocType", ["LIS"]),
]
CATEGORIES = {
    key: {"key": key, "label": label, "criteria": [{"name": name, "value": value}]}
    for key, label, name, value in _DEFINITIONS
}


def criteria(key, config):
    result = []
    for field, name in [
        ("state", "State"),
        ("city", "City"),
        ("zip_codes", "ZipFive"),
        ("county_fips", "FIPS"),
    ]:
        value = config.get(field)
        if value:
            result.append(
                {"name": name, "value": value if isinstance(value, list) else [value]}
            )
    category = deepcopy(CATEGORIES[key]["criteria"])
    if key == "high_equity":
        category[0]["value"] = [[config["high_equity_min"], None]]
    return result + category
