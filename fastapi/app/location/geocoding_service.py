import httpx

from app.location.service import get_zip_from_coordinates
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

HEADERS = {
    "User-Agent": "ListingIQ/1.0 (location-filter)"
}
async def resolve_place_to_zip(
    city: str,
    state: str | None = None,

):
    city = city.strip()
    if not city:
        return []
    # City is always required.
    # State is added only when the user selected one.
    query = city
    if state:
        # Frontend values are stored as "Illinois,IL". Nominatim should receive
        # a real state name/code, not the combined UI label.
        state = state.strip()
        if "," in state:
            state = state.split(",", 1)[0].strip()
        query = f"{city}, {state}"

    params = {
        "q": query,
        "format": "json",
        "limit": 50,
        "countrycodes": "us",
        "addressdetails": 1,
    }
    async with httpx.AsyncClient(
        timeout=10.0,
        headers=HEADERS,

    ) as client:
        response = await client.get(
            NOMINATIM_URL,
            params=params,
        )
    response.raise_for_status()
    results = response.json()
    if not results:
        return []
    locations = []



    for result in results:



        latitude = float(result["lat"])

        longitude = float(result["lon"])



        zip_results = get_zip_from_coordinates(

            latitude,

            longitude,

        )



        if not zip_results:

            continue



        # get_zip_from_coordinates can return one or many records.

        if isinstance(zip_results, dict):

            zip_results = [zip_results]



        for location in zip_results:



            locations.append({

                "state": location["state"],

                "state_code": location["state_code"],

                "zip": location["zip"],

                "city": location["city"],

                "county": location.get("county"),

                "latitude": latitude,

                "longitude": longitude,

            })



    # Remove duplicates

    unique_locations = {}



    for location in locations:



        key = (

            location["city"].lower(),

            location["state_code"],

            location["zip"],

        )



        unique_locations[key] = location



    return list(unique_locations.values())