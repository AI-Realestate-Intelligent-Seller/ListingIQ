import gzip
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"
DB_PATH = DATA_DIR / "zip_boundaries.sqlite"
GZ_PATH = DATA_DIR / "zip_boundaries.sqlite.gz"


def _ensure_db() -> Optional[Path]:
    """Decompress the committed .gz artifact on first run / fresh deploy."""
    if DB_PATH.exists():
        return DB_PATH
    if not GZ_PATH.exists():
        return None
    with gzip.open(GZ_PATH, "rb") as src, open(DB_PATH, "wb") as dst:
        shutil.copyfileobj(src, dst)
    return DB_PATH


_db_path = _ensure_db()


def _connection() -> Optional[sqlite3.Connection]:
    if _db_path is None or not _db_path.exists():
        return None
    return sqlite3.connect(str(_db_path))


def get_zip_boundaries(zip_codes: List[str]) -> Dict[str, dict]:
    """Return {zip: geojson_geometry} for every ZIP we have boundary data for."""
    conn = _connection()
    if conn is None or not zip_codes:
        return {}

    placeholders = ",".join("?" for _ in zip_codes)
    rows = conn.execute(
        f"SELECT zip, geometry FROM zip_boundaries WHERE zip IN ({placeholders})",
        zip_codes,
    ).fetchall()
    conn.close()

    return {zip_code: json.loads(geometry) for zip_code, geometry in rows}


def get_dissolved_boundary(zip_codes: List[str]) -> Optional[dict]:
    """Union every matched ZIP's polygon into one outline GeoJSON geometry."""
    boundaries = get_zip_boundaries(zip_codes)
    if not boundaries:
        return None

    geometries = [shape(geometry) for geometry in boundaries.values()]
    merged = unary_union(geometries)
    if merged.is_empty:
        return None

    return mapping(merged)
