import httpx
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Query, HTTPException
from ..location.service import get_fitlerData, get_zipcodes, get_all_zipcodes,get_cities,get_by_city,get_by_zip,get_by_county
from ..location.boundary_service import get_dissolved_boundary
from app.location.geocoding_service import build_query, get_geocoder, resolve_place_to_zip
from ..models import User
from .auth import get_current_user

router = APIRouter()


class ExactAddressRequest(BaseModel):
    address: str = Field(..., min_length=5, max_length=500)


@router.get("/filter")
def states():
    return get_fitlerData()


@router.get("/zipcodes/{state}")
def zipcodes(state: str):
    return get_zipcodes(state)

@router.get("/zipcodes")
def all_zipcodes():
    return get_all_zipcodes()


@router.get("/cities")
def cities(zipcode: str = None, state: str = None):
    if not zipcode and not state:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: zipcode, state",
        )
    return get_cities(zipcode=zipcode, state=state)



@router.get("/resolve")
async def resolve_location(
    city: str = Query(..., min_length=2),
    state: str | None = Query(None, min_length=2),
):
    result = await resolve_place_to_zip(city, state)

    if not result:
        return {
            "found": False,
            "data": None,
        }

    return {
        "found": True,
        "data": result,
    }


@router.post("/geocode")
def geocode_exact_address(
    payload: ExactAddressRequest,
    _current_user: User = Depends(get_current_user),
):
    """Resolve one complete street address for an authenticated map preview."""
    query = build_query(payload.address)
    if query is None:
        raise HTTPException(
            status_code=422,
            detail="Enter a complete street address including city, state, and ZIP code.",
        )
    try:
        result = get_geocoder().geocode(query)
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="The address service is unavailable. Please try again.",
        )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No reliable map match was found. Check the full address and ZIP code.",
        )
    return {
        "address": payload.address.strip(),
        "display_name": result.display_name,
        "latitude": result.latitude,
        "longitude": result.longitude,
        "provider": result.provider,
    }


@router.get("/by-city")
def by_city(city: str = Query(..., min_length=1)):
    """States + zips for a selected/typed city — the ONE route the city filter calls."""
    result = get_by_city(city)
    if not result["states"] and not result["zipcodes"]:
        raise HTTPException(status_code=404, detail=f"No location data for '{city}'")
    return result

@router.get("/by-zip")
def by_zip(zipcode: str = Query(..., min_length=5, max_length=10)):
    result = get_by_zip(zipcode)
    if not result["states"]:
        raise HTTPException(status_code=404, detail=f"No location data for ZIP '{zipcode}'")
    return result


@router.get("/boundary")
def boundary(zips: str = Query(..., min_length=5)):
    """Dissolved outline geometry (GeoJSON) for the given comma-separated ZIP codes."""
    zip_list = sorted({z.strip() for z in zips.split(",") if z.strip()})
    geometry = get_dissolved_boundary(zip_list)
    if not geometry:
        raise HTTPException(status_code=404, detail="No boundary data for given ZIP codes")
    return {"type": "Feature", "geometry": geometry, "properties": {}}


@router.get("/by-county")
def by_county(county: str = Query(..., min_length=1), state: str | None = None):
    result = get_by_county(county, state)
    if not result["zipcodes"]:
        raise HTTPException(status_code=404, detail=f"No location data for county '{county}'")
    return result
