"""
One-time / re-runnable build step: converts the Census cartographic boundary
shapefile for ZCTAs (ZIP Code Tabulation Areas) into a compact SQLite lookup
of {zip -> simplified GeoJSON geometry}, keyed to only the ZIP codes present
in data/knowledge/zip_data.csv (the set this app actually serves).

Source shapefile (not committed - download before running):
  https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_us_zcta520_500k.zip

Usage:
  python scripts/build_zip_boundaries.py /path/to/cb_2020_us_zcta520_500k.shp
"""
import csv
import gzip
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import shapefile  # pyshp
from shapely.geometry import shape, mapping

ROOT = Path(__file__).resolve().parents[1]
ZIP_CSV = ROOT / "data" / "knowledge" / "zip_data.csv"
OUT_DB = ROOT / "data" / "knowledge" / "zip_boundaries.sqlite"
OUT_GZ = ROOT / "data" / "knowledge" / "zip_boundaries.sqlite.gz"

# Simplify tolerance in degrees. The 500k cartographic file is already
# generalized; a light extra simplify keeps the DB small without visibly
# distorting shapes at the zoom levels this app's map is used at.
SIMPLIFY_TOLERANCE = 0.0008

# Coordinate precision in decimal degrees. 1e-5 deg is ~1.1m, far finer
# than this simplify tolerance already discards - rounding just shrinks
# the JSON text without losing visible detail.
COORD_DECIMALS = 5


def _round_coords(coords):
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], COORD_DECIMALS), round(coords[1], COORD_DECIMALS)]
    return [_round_coords(c) for c in coords]


def known_zips() -> set[str]:
    with open(ZIP_CSV, encoding="utf-8-sig", newline="") as f:
        return {row["zip"].strip() for row in csv.DictReader(f)}


def build(shp_path: Path) -> None:
    wanted = known_zips()
    print(f"Loaded {len(wanted)} known ZIP codes from {ZIP_CSV.name}")

    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    if OUT_DB.exists():
        OUT_DB.unlink()

    conn = sqlite3.connect(OUT_DB)
    conn.execute(
        "CREATE TABLE zip_boundaries (zip TEXT PRIMARY KEY, geometry TEXT NOT NULL)"
    )

    reader = shapefile.Reader(str(shp_path))
    fields = [f[0] for f in reader.fields[1:]]
    zip_field = "ZCTA5CE20" if "ZCTA5CE20" in fields else "ZCTA5CE10"

    matched = 0
    rows = []
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        zip_code = record.get(zip_field)
        if zip_code not in wanted:
            continue

        geom = shape(shape_record.shape.__geo_interface__)
        geom = geom.simplify(SIMPLIFY_TOLERANCE, preserve_topology=True)
        if geom.is_empty:
            continue

        geojson = mapping(geom)
        geojson["coordinates"] = _round_coords(geojson["coordinates"])
        rows.append((zip_code, json.dumps(geojson, separators=(",", ":"))))
        matched += 1
        if matched % 2000 == 0:
            print(f"  ...{matched} matched")

    conn.executemany(
        "INSERT INTO zip_boundaries (zip, geometry) VALUES (?, ?)", rows
    )
    conn.commit()
    conn.close()

    print(f"Matched {matched} / {len(wanted)} known ZIPs")
    print(f"Wrote {OUT_DB} ({OUT_DB.stat().st_size / 1_048_576:.1f} MB)")

    with open(OUT_DB, "rb") as src, gzip.open(OUT_GZ, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    print(f"Wrote {OUT_GZ} ({OUT_GZ.stat().st_size / 1_048_576:.1f} MB) - this is the file committed to git")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    build(Path(sys.argv[1]))
