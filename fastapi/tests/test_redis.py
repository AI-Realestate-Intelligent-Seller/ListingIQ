"""Redis connectivity. Skips when no server is running locally."""

import pytest

from app.core.config import settings

redis = pytest.importorskip('redis', reason='redis client not installed')


@pytest.fixture
def client():
    connection = redis.Redis.from_url(settings['redis_url'], socket_connect_timeout=2)
    try:
        connection.ping()
    except Exception as error:  # server not running locally
        pytest.skip(f'Redis unavailable at {settings["redis_url"]}: {error}')
    return connection


def test_redis_url_points_at_localhost():
    assert settings['redis_url'].startswith('redis://127.0.0.1:6379') \
        or settings['redis_url'].startswith('redis://localhost:6379')


def test_values_round_trip(client):
    client.set('ListingIQ:test:key', 'value', ex=30)
    assert client.get('ListingIQ:test:key') == b'value'
    client.delete('ListingIQ:test:key')
    assert client.get('ListingIQ:test:key') is None


def test_server_reports_a_version(client):
    info = client.info('server')
    assert info['redis_version']
    assert int(info['tcp_port']) == 6379
