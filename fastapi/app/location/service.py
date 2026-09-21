from collections import defaultdict
import csv
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
from scipy.spatial import cKDTree


CSV_PATH = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "knowledge"
    / "zip_data.csv"
)


# ==========================================
# Global In-Memory Lookups
# ==========================================

zip_to_data: Dict[str, Dict[str, str]] = {}

states_to_zips: Dict[
    str,
    List[Dict[str, str]]
] = defaultdict(list)

states: Dict[str, str] = {}


# ==========================================
# Coordinate Index
# ==========================================

coordinate_records = []
coordinate_points = []

zip_tree: Optional[cKDTree] = None


# ==========================================
# Load ZIP CSV
# ==========================================

with open(
    CSV_PATH,
    encoding="utf-8-sig",
    newline=""
) as f:

    reader = csv.DictReader(f)

    for row in reader:

        state = row["state"]
        state_code = row["state_code"]
        zip_code = row["zip"]
        city = row["city"]
        county = row["county"]
        latitude = row["latitude"]
        longitude = row["longitude"]

        states[state] = state_code

        
        # Build record once
    

        record = {
            "city": city,
            "zip": zip_code,
            "state": state,
            "state_code": state_code,
            "county": county,
            "latitude": latitude,
            "longitude": longitude,
        }

        zip_to_data[zip_code] = record

        states_to_zips[state].append(record)

        
        # Add coordinates to index
       

        try:
            lat = float(latitude)
            lng = float(longitude)

            coordinate_points.append(
                [lat, lng]
            )

            coordinate_records.append(record)

        except (ValueError, TypeError):
            pass

# Build KD-Tree ONCE


if coordinate_points:

    zip_tree = cKDTree(
        np.array(coordinate_points)
    )


print(
    f"Loaded {len(states_to_zips)} states into memory."
)

print(
    f"Loaded {len(zip_to_data)} zip codes into memory."
)

print(
    f"Built coordinate index with "
    f"{len(coordinate_records)} locations."
)

def get_fitlerData() -> Dict[str, List[str]]:

    states_list = [
        f"{state},{states[state]}"
        for state in sorted(states)
    ]

    cities_list = sorted({
        record["city"]
        for record in zip_to_data.values()
    })

    return {
        "states": states_list,
        "cities": cities_list
    }


def get_zipcodes(
    state: str
) -> List[Dict[str, str]]:

    return states_to_zips.get(
        state,
        []
    )


def get_all_zipcodes() -> List[Dict[str, str]]:

    return list(
        zip_to_data.values()
    )


def get_cities(
    zipcode: Optional[str] = None,
    state: Optional[str] = None
) -> List[str]:

    if zipcode and state:

        record = zip_to_data.get(zipcode)

        if record and record["state"] == state:
            return [record["city"]]

        return []

    if zipcode:

        record = zip_to_data.get(zipcode)

        return [
            record["city"]
        ] if record else []

    if state:

        return sorted({
            item["city"]
            for item in states_to_zips.get(
                state,
                []
            )
        })

    return []



# Find ZIP From Coordinates


def get_zip_from_coordinates(
    latitude: float,
    longitude: float
) -> Optional[Dict[str, str]]:

    if zip_tree is None:
        return None

    distance, index = zip_tree.query(
        [latitude, longitude],
        k=1
    )

    record = coordinate_records[index]

    return {
        "zip": record["zip"],
        "city": record["city"],
        "state": record["state"],
        "state_code": record["state_code"],
        "county": record["county"],
        "latitude": record["latitude"],
        "longitude": record["longitude"],
    }

def get_by_city(city_name: str) -> Dict[str, List]:
    """
    Returns associated states and zip records for a given city.
    Case-insensitive search.
    """
    matched_zips: List[Dict[str, str]] = []
    matched_states: Set[str] = set()

    target_city = city_name.strip().lower()

    for record in zip_to_data.values():
        if record["city"].lower() == target_city:
            matched_zips.append(record)
            # Standardized "State Name,State Code" format matching get_fitlerData
            matched_states.add(f"{record['state']},{record['state_code']}")

    return {
        "states": sorted(list(matched_states)),
        "zipcodes": matched_zips
    }




def get_by_zip(zipcode: str) -> Dict[str, List]:
    """Return the state/city represented by one ZIP code."""
    record = zip_to_data.get(zipcode.strip())
    if not record:
        return {"states": [], "cities": [], "zipcodes": []}

    return {
        "states": [f"{record['state']},{record['state_code']}"],
        "cities": [record["city"]],
        "zipcodes": [record],
    }

def get_cities(
    zipcode: Optional[str] = None,
    state: Optional[str] = None
) -> List[str]:

    if zipcode and state:

        record = zip_to_data.get(zipcode)

        if record and record["state"] == state:
            return [record["city"]]

        return []

    if zipcode:

        record = zip_to_data.get(zipcode)

        return [
            record["city"]
        ] if record else []

    if state:

        return sorted({
            item["city"]
            for item in states_to_zips.get(
                state,
                []
            )
        })

    return []


