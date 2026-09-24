"""Address normalization and configurable geocoding providers.

Only backend code talks to Nominatim. Lead map requests read coordinates that
the worker has already persisted, keeping page loads fast and public-service
requests conservative.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

import httpx

from app.core.config import settings
from app.leads.location import STATE_LABELS, parse as parse_location
from app.location.service import get_zip_from_coordinates


SPACE_RE = re.compile(r'\s+')
COMMA_RE = re.compile(r'\s*,\s*')


@dataclass(frozen=True)
class GeocodingQuery:
    address: str
    city: str = ''
    state: str = ''
    postal_code: str = ''
    country_code: str = 'us'


@dataclass(frozen=True)
class GeocodingResult:
    latitude: float
    longitude: float
    display_name: str
    provider: str
    importance: float | None = None


class Geocoder(Protocol):
    name: str

    def geocode(self, query: GeocodingQuery) -> GeocodingResult | None:
        """Return a verified best match, or None when no reliable match exists."""


def _clean(value: str | None) -> str:
    return SPACE_RE.sub(' ', (value or '').strip())


def build_query(property_address: str | None, area: str | None = None,
                country_code: str | None = None) -> GeocodingQuery | None:
    """Build a stable, sufficiently complete query from imported fields."""
    address = _clean(property_address)
    area = _clean(area)
    if not address:
        return None

    location = parse_location(address, area)
    has_street = bool(re.search(r'\d', address.split(',')[0]))
    has_locality = bool(location.city or location.state or location.postal or area)
    if not has_street or not has_locality:
        return None

    parts = [_clean(part) for part in COMMA_RE.split(address) if _clean(part)]
    lowered = {part.casefold() for part in parts}
    if area and area.casefold() not in lowered:
        parts.append(area)
    country = (country_code or settings['geocoding']['country'] or 'us').lower()
    if country in {'us', 'usa'} and not any(
            value in lowered for value in {'us', 'usa', 'united states'}):
        parts.append('United States')

    return GeocodingQuery(
        address=', '.join(parts),
        city=location.city,
        state=location.state,
        postal_code=location.postal,
        country_code=country,
    )


def normalize_address(property_address: str | None, area: str | None = None,
                      country_code: str | None = None) -> str | None:
    query = build_query(property_address, area, country_code)
    return query.address if query else None


def _fold(value: object) -> str:
    return _clean(str(value or '')).casefold()


def _candidate_score(item: dict, query: GeocodingQuery) -> float | None:
    address = item.get('address') if isinstance(item.get('address'), dict) else {}
    if query.country_code and _fold(address.get('country_code')) not in {
            '', query.country_code.casefold()}:
        return None

    score = float(item.get('importance') or 0)
    if query.postal_code:
        candidate_postcode = _fold(address.get('postcode')).split('-', 1)[0]
        if candidate_postcode and candidate_postcode != query.postal_code.casefold():
            return None
        score += 4 if candidate_postcode == query.postal_code.casefold() else 0
    if query.state:
        candidate_state = _fold(
            address.get('state_code') or address.get('ISO3166-2-lvl4') or address.get('state'))
        wanted_code = query.state.casefold()
        wanted_name = _fold(STATE_LABELS.get(query.state.upper()))
        accepted_states = {wanted_code, wanted_name, f'us-{wanted_code}'} - {''}
        if candidate_state and candidate_state not in accepted_states:
            return None
        score += 2 if candidate_state else 0
    if query.city:
        candidate_city = _fold(
            address.get('city') or address.get('town') or address.get('village')
            or address.get('municipality') or address.get('county'))
        wanted_city = query.city.casefold()
        if candidate_city == wanted_city:
            score += 2
        elif candidate_city and wanted_city not in candidate_city and candidate_city not in wanted_city:
            score -= 1
    return score


def parse_nominatim_results(results: object, query: GeocodingQuery) -> GeocodingResult | None:
    """Choose the strongest locality-consistent result; never guess coordinates."""
    if not isinstance(results, list):
        return None
    ranked: list[tuple[float, dict]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            latitude = float(item['lat'])
            longitude = float(item['lon'])
        except (KeyError, TypeError, ValueError):
            continue
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            continue
        score = _candidate_score(item, query)
        if score is not None:
            ranked.append((score, item))
    if not ranked:
        return None
    _, best = max(ranked, key=lambda pair: pair[0])
    return GeocodingResult(
        latitude=float(best['lat']),
        longitude=float(best['lon']),
        display_name=_clean(best.get('display_name')) or query.address,
        provider='nominatim',
        importance=float(best['importance']) if best.get('importance') is not None else None,
    )


class NominatimGeocoder:
    name = 'nominatim'

    def __init__(self, base_url: str | None = None, timeout: float | None = None,
                 user_agent: str | None = None):
        config = settings['geocoding']
        self.base_url = (base_url or config['nominatim_base_url']).rstrip('/')
        self.timeout = timeout or config['timeout_seconds']
        self.user_agent = user_agent or config['user_agent']

    def geocode(self, query: GeocodingQuery) -> GeocodingResult | None:
        with httpx.Client(timeout=self.timeout, headers={'User-Agent': self.user_agent}) as client:
            response = client.get(
                f'{self.base_url}/search',
                params={
                    'q': query.address,
                    'format': 'jsonv2',
                    'limit': 5,
                    'countrycodes': query.country_code,
                    'addressdetails': 1,
                },
            )
            response.raise_for_status()
            return parse_nominatim_results(response.json(), query)


def get_geocoder() -> Geocoder:
    provider = settings['geocoding']['provider'].strip().lower()
    if provider == 'nominatim':
        return NominatimGeocoder()
    raise ValueError(f'Unsupported geocoding provider: {provider}')


async def resolve_place_to_zip(city: str, state: str | None = None):
    """Existing location-picker helper, now sharing configured Nominatim settings."""
    city = city.strip()
    if not city:
        return []
    state = (state or '').strip()
    if ',' in state:
        state = state.split(',', 1)[0].strip()
    query = f'{city}, {state}' if state else city
    config = settings['geocoding']
    async with httpx.AsyncClient(
        timeout=config['timeout_seconds'],
        headers={'User-Agent': config['user_agent']},
    ) as client:
        response = await client.get(
            f"{config['nominatim_base_url']}/search",
            params={
                'q': query,
                'format': 'jsonv2',
                'limit': 50,
                'countrycodes': config['country'],
                'addressdetails': 1,
            },
        )
    response.raise_for_status()
    locations = []
    for result in response.json():
        try:
            latitude, longitude = float(result['lat']), float(result['lon'])
        except (KeyError, TypeError, ValueError):
            continue
        zip_results = get_zip_from_coordinates(latitude, longitude)
        if not zip_results:
            continue
        for location in ([zip_results] if isinstance(zip_results, dict) else zip_results):
            locations.append({
                'state': location['state'],
                'state_code': location['state_code'],
                'zip': location['zip'],
                'city': location['city'],
                'county': location.get('county'),
                'latitude': latitude,
                'longitude': longitude,
            })
    unique = {
        (item['city'].lower(), item['state_code'], item['zip']): item
        for item in locations
    }
    return list(unique.values())
