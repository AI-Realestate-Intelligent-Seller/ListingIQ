"""Geocoding queue and Lead Pool map-bound filtering."""

from dataclasses import dataclass

from app.location.geocoding_service import (
    GeocodingQuery,
    GeocodingResult,
    build_query,
    parse_nominatim_results,
)
from app.location.queue import prepare_lead, process_one
from app.leads import service as leads_service
from app.models import Lead


LEADS_URL = '/api/v1/leads'
EMAIL = 'maps@listingiq.test'


def test_address_normalization_requires_a_street_and_locality():
    query = build_query('  4517   W Adams St, Chicago, IL 60624  ', 'Austin')
    assert query is not None
    assert query.address == '4517 W Adams St, Chicago, IL 60624, Austin, United States'
    assert query.state == 'IL'
    assert query.postal_code == '60624'
    assert build_query('Chicago', None) is None


def test_nominatim_parser_rejects_wrong_postcode_and_chooses_matching_result():
    query = GeocodingQuery(
        '4517 W Adams St, Chicago, IL 60624, United States',
        city='Chicago', state='IL', postal_code='60624', country_code='us',
    )
    result = parse_nominatim_results([
        {'lat': '1', 'lon': '2', 'importance': 0.9,
         'address': {'postcode': '99999', 'state_code': 'IL', 'country_code': 'us'}},
        {'lat': '41.8801', 'lon': '-87.7401', 'importance': 0.4,
         'display_name': '4517 West Adams Street, Chicago, Illinois',
         'address': {'postcode': '60624', 'city': 'Chicago',
                     'state': 'Illinois', 'country_code': 'us'}},
    ], query)
    assert result is not None
    assert (result.latitude, result.longitude) == (41.8801, -87.7401)
    assert result.provider == 'nominatim'


@dataclass
class FakeGeocoder:
    result: GeocodingResult | None
    name: str = 'nominatim'
    calls: int = 0

    def geocode(self, _query):
        self.calls += 1
        return self.result


def test_queue_stores_success_and_reuses_unchanged_address(session, make_user):
    user = make_user(EMAIL, role='broker')
    lead = Lead(
        user_id=user.id, owner_name='Mapped', phone='+13125550101',
        property_address='4517 W Adams St, Chicago, IL 60624', area='Chicago',
        geocoding_status='pending',
    )
    session.add(lead)
    session.commit()
    fake = FakeGeocoder(GeocodingResult(41.88, -87.74, 'Chicago', 'nominatim'))

    assert process_one(session, fake) is True
    session.refresh(lead)
    assert lead.geocoding_status == 'success'
    assert (lead.latitude, lead.longitude) == (41.88, -87.74)
    assert lead.geocoded_at is not None

    prepare_lead(lead)
    session.commit()
    assert lead.geocoding_status == 'success'
    assert process_one(session, fake) is False
    assert fake.calls == 1

    lead.property_address = '4519 W Adams St, Chicago, IL 60624'
    prepare_lead(lead)
    assert lead.geocoding_status == 'pending'
    assert lead.latitude is None and lead.longitude is None


def test_queue_records_no_match_without_false_coordinates(session, make_user):
    user = make_user(EMAIL, role='broker')
    lead = Lead(
        user_id=user.id, property_address='9 Elm Rd, Mattoon, IL 61938',
        geocoding_status='pending',
    )
    session.add(lead)
    session.commit()

    assert process_one(session, FakeGeocoder(None)) is True
    session.refresh(lead)
    assert lead.geocoding_status == 'failed'
    assert lead.latitude is None and lead.longitude is None
    assert 'No reliable' in lead.geocoding_error


def _map_pool(client, make_user, auth_header, session):
    user = make_user(EMAIL, role='broker')
    headers = auth_header(EMAIL)
    session.add_all([
        Lead(user_id=user.id, owner_name='Chicago Expired', phone='+13125550101',
             property_address='1 W Madison St, Chicago, IL 60602', signals='expired',
             source='propertyradar', latitude=41.88, longitude=-87.63,
             geocoding_status='success'),
        Lead(user_id=user.id, owner_name='Springfield Probate', phone='+12175550101',
             property_address='1 E Adams St, Springfield, IL 62701', signals='probate',
             source='csv_import', latitude=39.80, longitude=-89.64,
             geocoding_status='success'),
        Lead(user_id=user.id, owner_name='Waiting', phone='+13125550102',
             property_address='2 W Madison St, Chicago, IL 60602', signals='expired',
             geocoding_status='pending'),
    ])
    session.commit()
    return headers


def test_service_bounds_combine_with_signals_and_exclude_missing_coordinates(
        session, make_user):
    user = make_user(EMAIL, role='broker')
    session.add_all([
        Lead(user_id=user.id, owner_name='Inside', phone='+13125550101',
             property_address='1 W Madison St, Chicago, IL 60602', signals='expired',
             latitude=41.88, longitude=-87.63, geocoding_status='success'),
        Lead(user_id=user.id, owner_name='Wrong signal', phone='+13125550102',
             property_address='2 W Madison St, Chicago, IL 60602', signals='probate',
             latitude=41.89, longitude=-87.64, geocoding_status='success'),
        Lead(user_id=user.id, owner_name='Waiting', phone='+13125550103',
             property_address='3 W Madison St, Chicago, IL 60602', signals='expired',
             geocoding_status='pending'),
    ])
    session.commit()

    rows = leads_service.list_leads(
        session, user, signals=['expired'], states=['IL'],
        north=42, south=41, east=-87, west=-88,
    )
    assert [row['owner_name'] for row in rows] == ['Inside']


def test_bound_validation_rejects_incomplete_malformed_and_reversed_boxes():
    for values in [
        (42, None, None, None),
        (91, 41, -87, -88),
        (40, 41, -87, -88),
        (42, 41, -89, -88),
    ]:
        try:
            leads_service.validate_bounds(*values)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Expected invalid bounds: {values}')
    leads_service.validate_bounds(42, 41, -87, -88)


def test_polygon_validation_and_filtering(session, make_user):
    polygon = leads_service.validate_polygon([
        [42.0, -88.0], [42.0, -87.0], [41.0, -87.0], [41.0, -88.0],
    ])
    user = make_user(EMAIL, role='broker')
    session.add_all([
        Lead(user_id=user.id, owner_name='Inside polygon', phone='+13125550111',
             property_address='1 W Madison St, Chicago, IL 60602', signals='expired',
             latitude=41.88, longitude=-87.63, geocoding_status='success'),
        Lead(user_id=user.id, owner_name='Outside polygon', phone='+12175550111',
             property_address='1 E Adams St, Springfield, IL 62701', signals='expired',
             latitude=39.80, longitude=-89.64, geocoding_status='success'),
    ])
    session.commit()
    rows = leads_service.list_leads(session, user, signals=['expired'], polygon=polygon)
    assert [row['owner_name'] for row in rows] == ['Inside polygon']

    for invalid in [[], [[1, 2], [3, 4]], [[1, 2], [1, 2], [1, 2]], [[91, 2], [1, 2], [2, 3]]]:
        try:
            leads_service.validate_polygon(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f'Expected invalid polygon: {invalid}')


def test_bounding_box_combines_with_existing_filters(
        client, make_user, auth_header, session):
    headers = _map_pool(client, make_user, auth_header, session)
    response = client.get(LEADS_URL, headers=headers, params={
        'north': 42, 'south': 41, 'east': -87, 'west': -88,
        'signal': 'expired', 'state': 'IL',
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert [lead['owner_name'] for lead in body['leads']] == ['Chicago Expired']
    assert body['map']['mappable_count'] == 1


def test_bounds_require_all_values_and_valid_order(client, make_user, auth_header):
    make_user(EMAIL, role='broker')
    headers = auth_header(EMAIL)
    incomplete = client.get(LEADS_URL, headers=headers, params={'north': 42})
    assert incomplete.status_code == 422
    reversed_lat = client.get(LEADS_URL, headers=headers, params={
        'north': 40, 'south': 41, 'east': -87, 'west': -88,
    })
    assert reversed_lat.status_code == 422


def test_missing_coordinates_stay_in_table_but_not_bounded_results(
        client, make_user, auth_header, session):
    headers = _map_pool(client, make_user, auth_header, session)
    unbounded = client.get(LEADS_URL, headers=headers).json()
    assert len(unbounded['leads']) == 3
    assert unbounded['map'] == {'mappable_count': 2, 'pending_count': 1, 'failed_count': 0}

    bounded = client.get(LEADS_URL, headers=headers, params={
        'north': 90, 'south': -90, 'east': 180, 'west': -180,
    }).json()
    assert len(bounded['leads']) == 2
