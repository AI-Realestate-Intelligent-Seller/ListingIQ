"""Redis connectivity. Skips when no server is running locally."""

from urllib.parse import urlsplit

import pytest

from app.core.config import settings

redis = pytest.importorskip('redis', reason='redis client not installed')


def safe_redis_location(url: str) -> str:
    """Scheme, host and port only. The URL can carry a password."""
    try:
        parts = urlsplit(url)
        port = f':{parts.port}' if parts.port else ''
        return f'{parts.scheme}://{parts.hostname or "?"}{port}'
    except ValueError:
        return '<unparseable redis url>'


@pytest.fixture
def client():
    connection = redis.Redis.from_url(settings['redis_url'], socket_connect_timeout=2)
    try:
        connection.ping()
    except Exception as error:  # server not running locally
        pytest.skip(f'Redis unavailable at {safe_redis_location(settings["redis_url"])} '
                    f'({type(error).__name__})')
    return connection


def test_redis_url_points_at_localhost():
    url = settings['redis_url']
    # A plain boolean keeps pytest's assertion detail from printing the URL.
    points_at_localhost = (url.startswith('redis://127.0.0.1:6379')
                           or url.startswith('redis://localhost:6379'))
    assert points_at_localhost, f'REDIS_URL points at {safe_redis_location(url)}'


def test_values_round_trip(client):
    client.set('ListingIQ:test:key', 'value', ex=30)
    assert client.get('ListingIQ:test:key') == b'value'
    client.delete('ListingIQ:test:key')
    assert client.get('ListingIQ:test:key') is None


def test_server_reports_a_version(client):
    info = client.info('server')
    assert info['redis_version']
    assert int(info['tcp_port']) == 6379
