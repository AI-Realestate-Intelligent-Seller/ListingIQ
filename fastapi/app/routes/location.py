from fastapi import APIRouter,Query,HTTPException
from ..location.service import get_fitlerData, get_zipcodes, get_all_zipcodes,get_cities,get_by_city,get_by_zip
from app.location.geocoding_service import resolve_place_to_zip

router = APIRouter()


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
