import requests

from app.core.config import settings
from app.sms import number_lookup


class FakeResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {
            'data': {
                'phone_number': '+17732341243',
                'country_code': 'US',
                'national_format': '(773) 234-1243',
                'carrier': {'name': 'Example Wireless', 'type': 'mobile'},
                'portability': {'line_type': 'mobile'},
            },
        }


def test_live_lookup_uses_telnyx_and_accepts_only_explicit_mobile(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured.update(url=url, headers=headers, timeout=timeout)
        return FakeResponse()

    monkeypatch.setitem(settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(settings['telnyx'], 'api_key', 'test-key')
    monkeypatch.setattr(number_lookup.requests, 'get', fake_get)

    result = number_lookup.lookup_number('+17732341243')

    assert result.is_mobile is True
    assert result.line_type == 'mobile'
    assert result.carrier_name == 'Example Wireless'
    assert captured['url'].endswith('/+17732341243')
    assert captured['headers']['Authorization'] == 'Bearer test-key'
    assert captured['timeout'] == 10


def test_live_lookup_rejects_a_verified_non_mobile_line(monkeypatch):
    class LandlineResponse(FakeResponse):
        @staticmethod
        def json():
            return {
                'data': {
                    'phone_number': '+17732341243',
                    'carrier': {'name': 'Example Telecom', 'type': 'mobile'},
                    'portability': {'line_type': 'landline'},
                },
            }

    monkeypatch.setitem(settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(settings['telnyx'], 'api_key', 'test-key')
    monkeypatch.setattr(number_lookup.requests, 'get', lambda *args, **kwargs: LandlineResponse())

    result = number_lookup.lookup_number('+17732341243')

    assert result.status == 'verified'
    assert result.line_type == 'landline'
    assert result.is_mobile is False


def test_live_lookup_failure_is_fail_closed(monkeypatch):
    monkeypatch.setitem(settings['telnyx'], 'mode', 'telnyx')
    monkeypatch.setitem(settings['telnyx'], 'api_key', 'test-key')
    monkeypatch.setattr(
        number_lookup.requests,
        'get',
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout()),
    )

    result = number_lookup.lookup_number('+17732341243')

    assert result.status == 'error'
    assert result.error_code == 'request_failed'
    assert result.is_mobile is False


def test_simulation_never_calls_telnyx(monkeypatch):
    monkeypatch.setitem(settings['telnyx'], 'mode', 'simulation')
    monkeypatch.setattr(
        number_lookup.requests,
        'get',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('network call')),
    )

    result = number_lookup.lookup_number('+17732341243')

    assert result.status == 'simulation_bypass'
    assert result.enforces_mobile_gate is False
