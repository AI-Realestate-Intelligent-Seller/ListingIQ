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
        self.mget_calls = 0
        self.get_calls = 0

    def _check(self):
        if self.down:
            raise redis.ConnectionError('connection refused')

    def ping(self):
        self._check()
        return True

    def get(self, key):
        self._check()
        self.get_calls += 1
        return self.store.get(key)

    def mget(self, keys):
        self._check()
        self.mget_calls += 1
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
    """Stands in for a tracked dashboard table (own metadata, in-memory DB)."""
    __tablename__ = 'leads'
    id = Column(Integer, primary_key=True)
    name = Column(String)
    conversation_id = Column(Integer, nullable=True)


class Campaign(Base):
    __tablename__ = 'campaigns'
    id = Column(Integer, primary_key=True)
    status = Column(String, default='draft')


class Untracked(Base):
    __tablename__ = 'notifications'
    id = Column(Integer, primary_key=True)


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
        first = cache.get_or_load('demo', ['leads'], {'user': 1}, load)
        second = cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert first == second == {'rows': [1, 2]}
    assert calls['n'] == 1
    assert 'cache miss ns=demo source=db' in caplog.text
    assert 'cache hit ns=demo source=redis' in caplog.text


def test_key_is_scoped_by_caller_and_parameters(fake):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    cache.get_or_load('demo', ['leads'], {'user': 2}, load)
    cache.get_or_load('demo', ['leads'], {'user': 1, 'scope': 'all'}, load)
    assert calls['n'] == 3


def test_commit_invalidates_dependent_payloads(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    db.add(Widget(name='a'))
    db.commit()
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 2


def test_bulk_update_invalidates(fake, db):
    db.add(Widget(name='a'))
    db.commit()
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    db.execute(update(Widget).values(name='b'))
    db.commit()
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 2


def test_untracked_table_write_keeps_cache_and_skips_redis(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    db.add(Untracked())
    db.commit()
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 1
    assert not any(key.endswith('notifications') for key in fake.store)


def test_a_hit_is_one_redis_round_trip(fake):
    load, _ = counting_loader([1])
    cache.get_or_load('demo', ['leads', 'messages'], {'user': 1}, load)
    fake.mget_calls = fake.get_calls = 0
    cache.get_or_load('demo', ['leads', 'messages'], {'user': 1}, load)
    assert (fake.mget_calls, fake.get_calls) == (1, 0)


def test_write_during_load_is_never_served(fake, db):
    def load_while_someone_writes():
        db.add(Widget(name='late'))
        db.commit()
        return ['built before the write']

    cache.get_or_load('demo', ['leads'], {'user': 1}, load_while_someone_writes)
    load, calls = counting_loader(['fresh'])
    assert cache.get_or_load('demo', ['leads'], {'user': 1}, load) == ['fresh']
    assert calls['n'] == 1


def test_rollback_does_not_invalidate(fake, db):
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    db.add(Widget(name='a'))
    db.flush()
    db.rollback()
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 1


def test_redis_outage_falls_back_to_db(fake, caplog):
    fake.down = True
    load, calls = counting_loader([1])
    with caplog.at_level(logging.INFO, logger='app.cache'):
        assert cache.get_or_load('demo', ['leads'], {'user': 1}, load) == [1]
        assert cache.get_or_load('demo', ['leads'], {'user': 1}, load) == [1]
    assert calls['n'] == 2
    assert 'Redis cache unavailable' in caplog.text
    assert 'source=db reason=redis-unavailable' in caplog.text


def test_disabled_cache_never_touches_redis(fake, monkeypatch):
    monkeypatch.setitem(settings, 'cache', {'enabled': False})
    load, calls = counting_loader([1])
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    cache.get_or_load('demo', ['leads'], {'user': 1}, load)
    assert calls['n'] == 2
    assert fake.store == {}


THREADS = cache.CONVERSATION_TABLES


def test_a_draft_does_not_clear_thread_pages_but_clears_lead_pool(fake, db):
    followups, followup_calls = counting_loader(['threads'])
    pool, pool_calls = counting_loader(['pool'])
    cache.get_or_load('followups', THREADS, {'user': 1}, followups)
    cache.get_or_load('lead-pool', cache.DASHBOARD_TABLES, {'user': 1}, pool)

    # What a draft writes: an unsent campaign and leads with no thread yet.
    db.add_all([Campaign(status='draft'), Widget(name='recipient')])
    db.commit()

    cache.get_or_load('followups', THREADS, {'user': 1}, followups)
    cache.get_or_load('lead-pool', cache.DASHBOARD_TABLES, {'user': 1}, pool)
    assert followup_calls['n'] == 1
    assert pool_calls['n'] == 2


def test_a_lead_on_a_thread_clears_thread_pages(fake, db):
    lead = Widget(name='owner', conversation_id=None)
    db.add(lead)
    db.commit()
    load, calls = counting_loader(['threads'])
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    lead.conversation_id = 18          # campaign sent: the lead gets its thread
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    lead.name = 'renamed'              # any later edit of a threaded lead
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    assert calls['n'] == 3


def test_sending_a_campaign_clears_thread_pages(fake, db):
    campaign = Campaign(status='draft')
    db.add(campaign)
    db.commit()
    load, calls = counting_loader(['threads'])
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    campaign.status = 'sent'
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    assert calls['n'] == 2


def test_bulk_lead_update_clears_thread_pages(fake, db):
    db.add(Widget(name='a'))
    db.commit()
    load, calls = counting_loader(['threads'])
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    db.execute(update(Widget).values(name='b'))
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    assert calls['n'] == 2


def test_real_draft_creation_spares_thread_pages(monkeypatch, make_user, session):
    """End to end with the app's own models and create_draft (no Redis needed)."""
    from app.leads import campaign as campaign_service
    from app.models import Lead

    owner = make_user('cache-draft@example.test', role='broker')
    leads = [Lead(user_id=owner.id, owner_name=f'Owner {index}', phone=f'+1312555{index:04d}',
                  property_address=f'{index} Cache Ave', area='Chicago', signals='expired',
                  outreach_reason='Matches the imported vendor criteria', source='csv_import',
                  score=50, dnc=False) for index in range(3)]
    session.add_all(leads)
    session.commit()

    bumped: list[set[str]] = []
    monkeypatch.setattr(cache, 'bump', lambda tables: bumped.append(set(tables)))
    campaign_service.create_draft(session, owner, [lead.id for lead in leads], 'Cache draft', 'Hi {{first_name}}')
    session.commit()

    written = set().union(*bumped)
    assert {'campaigns', 'leads'} <= written                  # Lead Pool / Campaigns refresh
    assert not written & {cache.LEADS_WITH_THREAD, cache.SENT_CAMPAIGNS}   # Follow-ups / SMS stay cached


def test_bulk_update_limited_to_threadless_leads_spares_thread_pages(fake, db):
    db.add(Widget(name='a'))
    db.commit()
    load, calls = counting_loader(['threads'])
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    db.query(Widget).filter(Widget.conversation_id.is_(None)).update(
        {Widget.name: 'locked'}, synchronize_session=False)
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    assert calls['n'] == 1


def test_bulk_update_that_links_threads_still_clears(fake, db):
    db.add(Widget(name='a'))
    db.commit()
    load, calls = counting_loader(['threads'])
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    db.query(Widget).filter(Widget.conversation_id.is_(None)).update(
        {Widget.conversation_id: 18}, synchronize_session=False)
    db.commit()
    cache.get_or_load('followups', THREADS, {'user': 1}, load)
    assert calls['n'] == 2


def test_peek_returns_only_a_current_payload_and_never_loads(fake, db):
    assert cache.peek('demo', ['leads'], {'user': 1}) is None          # nothing cached
    cache.get_or_load('demo', ['leads'], {'user': 1}, lambda: {'rows': [1]})
    assert cache.peek('demo', ['leads'], {'user': 1}) == {'rows': [1]}  # current
    assert cache.peek('demo', ['leads'], {'user': 2}) is None          # other caller
    db.add(Widget(name='new'))
    db.commit()
    assert cache.peek('demo', ['leads'], {'user': 1}) is None          # stale after a write
