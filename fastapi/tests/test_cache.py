"""Redis read-through cache: hits, misses, commit-driven invalidation, outages."""

import logging

import pytest
import redis
from sqlalchemy import Column, Integer, String, create_engine, update
from sqlalchemy.orm import declarative_base, sessionmaker

from app import cache
from app.core.config import settings


class FakeRedis:
    """The handful of commands app.cache uses, held in a dict."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.down = False

    def _check(self):
        if self.down:
            raise redis.ConnectionError('connection refused')

    def ping(self):
        self._check()
        return True

    def get(self, key):
        self._check()
        return self.store.get(key)

    def mget(self, keys):
        self._check()
        return [self.store.get(key) for key in keys]

    def set(self, key, value, ex=None):  # noqa: ARG002
        self._check()
        self.store[key] = value

    def incr(self, key):
        self._check()
        self.store[key] = str(int(self.store.get(key, '0')) + 1)
        return int(self.store[key])

    def pipeline(self, transaction=False):  # noqa: ARG002
        fake = self

        class Pipe:
            def __init__(self):
                self.ops = []

            def incr(self, key):
                self.ops.append(key)

            def execute(self):
                return [fake.incr(key) for key in self.ops]

        return Pipe()


Base = declarative_base()


class Widget(Base):
    __tablename__ = 'widgets'
    id = Column(Integer, primary_key=True)
    name = Column(String)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setitem(settings, 'cache', {'enabled': True, 'ttl_seconds': 60, 'retry_seconds': 30})
    monkeypatch.setattr(cache.redis.Redis, 'from_url', classmethod(lambda cls, *a, **k: fake))
    cache._connection.reset()
    yield fake
    cache._connection.reset()


@pytest.fixture
def db():
    engine = create_engine('sqlite://', future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as session:
        yield session


def counting_loader(value):
    calls = {'n': 0}

    def load():
        calls['n'] += 1
        return value

    return load, calls


def test_second_read_is_served_from_redis(fake, caplog):
    load, calls = counting_loader({'rows': [1, 2]})
    with caplog.at_level(logging.INFO, logger='app.cache'):
        first = cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
        second = cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    assert first == second == {'rows': [1, 2]}
    assert calls['n'] == 1
    assert 'cache miss ns=demo source=db' in caplog.text
    assert 'cache hit ns=demo source=redis' in caplog.text


def test_key_is_scoped_by_caller_and_parameters(fake):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    cache.get_or_load('demo', ['widgets'], {'user': 2}, load)
    cache.get_or_load('demo', ['widgets'], {'user': 1, 'scope': 'all'}, load)
    assert calls['n'] == 3


def test_commit_invalidates_dependent_payloads(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    db.add(Widget(name='a'))
    db.commit()
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    assert calls['n'] == 2


def test_bulk_update_invalidates(fake, db):
    db.add(Widget(name='a'))
    db.commit()
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    db.execute(update(Widget).values(name='b'))
    db.commit()
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    assert calls['n'] == 2


def test_unrelated_table_write_keeps_cache(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    db.add(Widget(name='a'))
    db.commit()
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 1


def test_rollback_does_not_invalidate(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    db.add(Widget(name='a'))
    db.flush()
    db.rollback()
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    assert calls['n'] == 1


def test_redis_outage_falls_back_to_db(fake, caplog):
    fake.down = True
    load, calls = counting_loader([1])
    with caplog.at_level(logging.INFO, logger='app.cache'):
        assert cache.get_or_load('demo', ['widgets'], {'user': 1}, load) == [1]
        assert cache.get_or_load('demo', ['widgets'], {'user': 1}, load) == [1]
    assert calls['n'] == 2
    assert 'Redis cache unavailable' in caplog.text
    assert 'source=db reason=redis-unavailable' in caplog.text


def test_disabled_cache_never_touches_redis(fake, monkeypatch):
    monkeypatch.setitem(settings, 'cache', {'enabled': False})
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    cache.get_or_load('demo', ['widgets'], {'user': 1}, load)
    assert calls['n'] == 2
    assert fake.store == {}
