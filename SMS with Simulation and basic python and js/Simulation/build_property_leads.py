#!/usr/bin/env python3
"""Build 10 high-quality simulation leads from every workbook in the data ZIP."""
import csv
import io
import json
import re
import sys
import zipfile
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "source" / "Data-20260806T140344Z-1-001.zip"
OUTPUT = ROOT / "data" / "generated" / "property_leads_50.csv"
EXPIRED_OUTPUT = ROOT / "data" / "generated" / "expired_listing_leads_10.csv"


def clean(value):
    return "" if value is None else str(value).strip()


def first_name(owner):
    owner = clean(owner)
    if not owner:
        return "Owner"
    first = owner.split(",", 1)[1].strip().split()[0] if "," in owner else owner.split()[0]
    return first.title() if first.isalpha() else "Owner"


def source_name(filename):
    lowered = filename.lower()
    if "expired" in lowered: return "Expired listing"
    if "fsbo" in lowered: return "For sale by owner"
    if "pre-foreclosure" in lowered: return "Pre-foreclosure"
    if "divorce" in lowered: return "Property ownership change"
    return "Property owner outreach"


def reason_for(source):
    return {
        "Expired listing": "The property appears to have come off the market without a recorded sale.",
        "For sale by owner": "The property appears to have been offered for sale by its owner.",
        "Pre-foreclosure": "The source data identifies the property as a pre-foreclosure lead.",
        "Property ownership change": "The source data identifies the owner as a divorce-related property lead.",
        "Property owner outreach": "The source data identifies this owner as a cash-buyer/property lead.",
    }[source]


def initial_message(name, address, source):
    if source == "Expired listing":
        body = f"I saw {address} came off the market and wondered if it sold privately or may still be available?"
    elif source == "For sale by owner":
        body = f"I saw {address} may be for sale by owner. Are you still considering a sale?"
    elif source == "Pre-foreclosure":
        body = f"I’m reaching out because my property data flags {address} as pre-foreclosure. Would a quick call about your options help?"
    elif source == "Property ownership change":
        body = f"I’m reaching out because my property data lists {address} as a divorce-related lead. Would a private call about selling options help?"
    elif source == "Property owner outreach":
        body = f"I’m reaching out because my property data identifies {address} as a cash-buyer lead. Are you considering another sale?"
    else:
        body = f"I’m reaching out about {address}. Would you consider selling?"
    return f"Hey {name}, {body} Bobbie Fisher – RE/MAX"


def score(row):
    useful = ("owner", "address", "city", "state", "zip", "type", "beds", "baths", "sq_ft", "yr_built", "listing_price", "listing_dom")
    points = sum(bool(clean(row.get(key))) for key in useful)
    if first_name(row.get("owner")) != "Owner": points += 3
    if clean(row.get("address")) and clean(row.get("city")): points += 3
    return points


def workbook_rows(blob):
    workbook = load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    values = sheet.iter_rows(values_only=True)
    headers = [clean(value).lower() for value in next(values)]
    rows = [dict(zip(headers, values)) for values in values]
    workbook.close()
    return rows


def main():
    output = []
    with zipfile.ZipFile(SOURCE) as archive:
        files = sorted(name for name in archive.namelist() if name.lower().endswith(".xlsx"))
        for filename in files:
            source = source_name(filename)
            candidates = [row for row in workbook_rows(archive.read(filename)) if clean(row.get("address")) and clean(row.get("owner"))]
            candidates.sort(key=score, reverse=True)
            for row in candidates[:10]:
                name = first_name(row.get("owner"))
                city = clean(row.get("city")).title()
                state = clean(row.get("state")).upper() or "IL"
                zip_code = re.sub(r"\.0$", "", clean(row.get("zip")))
                address = ", ".join(part for part in [clean(row.get("address")).title(), city, state, zip_code] if part)
                facts = {key: clean(row.get(key)) for key in (
                    "type", "beds", "baths", "sq_ft", "yr_built", "owner_occ", "listed_for_sale",
                    "listed_for_sale_by_owner", "listing_price", "listing_dom", "purchase_date", "purchase_amt"
                ) if clean(row.get(key))}
                output.append({
                    "Name": name,
                    "Phone Number": f"+1312555{100 + len(output):04d}",
                    "Property Address": address,
                    "Lead Source": source,
                    "Outreach Reason": reason_for(source),
                    "Lead Details": json.dumps(facts, separators=(",", ":")),
                    "Initial Message": initial_message(name, address, source),
                })
    with OUTPUT.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=output[0].keys())
        writer.writeheader()
        writer.writerows(output)
    expired = [row for row in output if row["Lead Source"] == "Expired listing"]
    with EXPIRED_OUTPUT.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=output[0].keys())
        writer.writeheader()
        writer.writerows(expired)
    print(f"Created {OUTPUT} with {len(output)} leads")
    print(f"Created {EXPIRED_OUTPUT} with {len(expired)} expired-listing leads")


if __name__ == "__main__":
    main()
