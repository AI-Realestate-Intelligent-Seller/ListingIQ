"""Redis read-through cache for expensive API payloads.

How a cached read works
-----------------------
``get_or_load(namespace, tables, key_parts, loader)`` keys the payload by the
namespace and the caller's scope (user id + request parameters), and stores it
prefixed with the *versions* of every table it was built from. One ``MGET``
returns the current table versions and the stored payload together, so a hit
costs a single Redis round trip; the payload counts only when the versions it
was built from are still current. A miss runs ``loader()`` against the
database and stores the result with a TTL.

How invalidation works
----------------------
Every table has a version counter in Redis. SQLAlchemy session events record
which tables a transaction wrote to (ORM flushes *and* bulk
``update()``/``delete()``/``insert()`` statements), and after the commit those
counters are incremented. Any cached payload that depends on a changed table
now has a key nobody asks for, so the next read goes to the database. Because
the hook sits on the session itself, writes from routes, webhooks and
background workers all invalidate the cache without per-endpoint code. The TTL
is only a backstop for anything that bypasses the ORM.

Redis is optional
-----------------
If Redis is unreachable the cache steps aside: reads go straight to the
database and writes skip the version bump. After a failure Redis is not tried
again for ``CACHE_RETRY_SECONDS``, so an outage does not add a connection
timeout to every request.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Callable, Iterable, TypeVar
from urllib.parse import urlsplit

import redis
from fastapi.encoders import jsonable_encoder
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from .core.config import settings
from .logger import get_logger

logger = get_logger(__name__)

T = TypeVar('T')

KEY_PREFIX = 'listingiq:cache:'
VERSION_PREFIX = f'{KEY_PREFIX}ver:'
_DIRTY_KEY = 'listingiq_cache_dirty_tables'

# Tables the dashboard read endpoints are built from. A write to any of them
# invalidates every payload that declares it. Writes to other tables (persona
# index jobs, notifications, integrations, …) never touch Redis.
DASHBOARD_TABLES: tuple[str, ...] = (
    'users',
    'invitations',
    'conversations',
    'messages',
    'bookings',
    'campaigns',
    'outbound_sms_jobs',
    'leads',
    'lead_events',
    'lofty_exports',
)


def _settings() -> dict[str, Any]:
    return settings.get('cache', {})


def enabled() -> bool:
    return bool(_settings().get('enabled', True))


def _safe_location(url: str) -> str:
    """Scheme, host, port and db only — the URL can carry a password."""
    try:
        parts = urlsplit(url)
        port = f':{parts.port}' if parts.port else ''
        return f'{parts.scheme}://{parts.hostname or "?"}{port}{parts.path or ""}'
    except ValueError:
        return '<unparseable redis url>'


class _Connection:
    """One lazily-created client with outage back-off and state-change logging."""

    def __init__(self) -> None:
        self._client: redis.Redis | None = None
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._connected = False

    def get(self) -> redis.Redis | None:
        if not enabled() or time.monotonic() < self._down_until:
            return None
        if self._client is not None and self._connected:
            return self._client
        with self._lock:
            if self._client is None:
                self._client = redis.Redis.from_url(
                    settings['redis_url'],
                    socket_connect_timeout=1,
                    socket_timeout=1,
                    decode_responses=True,
                    health_check_interval=30,
                )
            try:
                self._client.ping()
            except redis.RedisError as error:
                self.mark_down(error)
                return None
            if not self._connected:
                self._connected = True
                logger.info('Redis cache connected url=%s ttl=%ss',
                            _safe_location(settings['redis_url']), ttl_seconds())
            return self._client

    def mark_down(self, error: Exception) -> None:
        retry = int(_settings().get('retry_seconds', 30))
        if self._connected or self._down_until == 0.0:
            logger.warning('Redis cache unavailable url=%s error=%s: %s — serving from DB, '
                           'retrying in %ss', _safe_location(settings['redis_url']),
                           type(error).__name__, error, retry)
        self._connected = False
        self._down_until = time.monotonic() + retry

    def reset(self) -> None:
        """Forget the client (tests, or after settings change)."""
        with self._lock:
            self._client = None
            self._connected = False
            self._down_until = 0.0


_connection = _Connection()


def client() -> redis.Redis | None:
    return _connection.get()


# Finer-grained keys for pages that only see conversation threads. A draft
# campaign locks leads that have no thread yet and is itself unsent, so it
# must not clear Follow-ups or the SMS inbox:
#   leads:conversation — a lead linked (before or after the write) to a thread
#   campaigns:sent     — a campaign that is, or was, sent
LEADS_WITH_THREAD = 'leads:conversation'
SENT_CAMPAIGNS = 'campaigns:sent'

# Pages built only from conversation threads (Follow-ups, SMS inbox).
CONVERSATION_TABLES: tuple[str, ...] = (
    'users', 'conversations', 'messages', LEADS_WITH_THREAD, SENT_CAMPAIGNS,
)

TRACKED_TABLES = frozenset((*DASHBOARD_TABLES, LEADS_WITH_THREAD, SENT_CAMPAIGNS))


def ttl_seconds() -> int:
    return max(1, int(_settings().get('ttl_seconds', 900)))


def check_connection() -> bool:
    """Ping once and log the outcome. Called at startup."""
    if not enabled():
        logger.info('Redis cache disabled (CACHE_ENABLED=false) — all reads come from DB')
        return False
    return client() is not None


# --------------------------------------------------------------------------- reads

def _fingerprint(parts: Any) -> str:
    raw = json.dumps(jsonable_encoder(parts), sort_keys=True, separators=(',', ':'))
    return hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]


def _version_keys(tables: Iterable[str]) -> list[str]:
    return [f'{VERSION_PREFIX}{name}' for name in sorted(set(tables))]


def get_or_load(
    namespace: str,
    tables: Iterable[str],
    key_parts: Any,
    loader: Callable[[], T],
    ttl: int | None = None,
) -> T | Any:
    """Return the cached payload, or build it with ``loader`` and cache it.

    ``key_parts`` must capture everything that changes the result for this
    caller — always include the user id, plus every request parameter.
    A hit returns the JSON-decoded payload (datetimes become ISO strings, which
    is exactly what FastAPI would have sent).
    """
    started = time.perf_counter()
    conn = client()
    if conn is None:
        result = loader()
        logger.info('cache bypass ns=%s source=db reason=redis-unavailable ms=%.1f',
                    namespace, (time.perf_counter() - started) * 1000)
        return result

    key = f'{KEY_PREFIX}{namespace}:{_fingerprint(key_parts)}'
    version_keys = _version_keys(tables)
    try:
        # One round trip: every table version, then the stored payload.
        *versions, stored = conn.mget([*version_keys, key])
    except redis.RedisError as error:
        _connection.mark_down(error)
        result = loader()
        logger.info('cache bypass ns=%s source=db reason=redis-error ms=%.1f',
                    namespace, (time.perf_counter() - started) * 1000)
        return result

    stamp = '.'.join(value or '0' for value in versions)
    label = f'{key.removeprefix(KEY_PREFIX)} versions={stamp}'
    if stored is not None:
        built_from, _, raw = stored.partition('\n')
        if built_from == stamp:
            payload = json.loads(raw)
            logger.info('cache hit ns=%s source=redis key=%s ms=%.1f',
                        namespace, label, (time.perf_counter() - started) * 1000)
            return payload

    result = loader()
    db_ms = (time.perf_counter() - started) * 1000
    try:
        # Stamped with the versions read *before* loading: a write that lands
        # while the query runs bumps a version, so this entry is never served.
        body = json.dumps(jsonable_encoder(result), separators=(',', ':'))
        conn.set(key, f'{stamp}\n{body}', ex=ttl or ttl_seconds())
    except (redis.RedisError, TypeError, ValueError) as error:
        logger.warning('cache store failed ns=%s error=%s: %s', namespace,
                       type(error).__name__, error)
    reason = 'stale' if stored is not None else 'empty'
    logger.info('cache miss ns=%s source=db reason=%s key=%s ms=%.1f',
                namespace, reason, label, db_ms)
    return result


def peek(namespace: str, tables: Iterable[str], key_parts: Any) -> Any | None:
    """The cached payload if it is current, else None. Never touches the DB.

    Lets a ``/load`` endpoint answer at once on a hit instead of starting a
    background job the browser then has to poll.
    """
    started = time.perf_counter()
    conn = client()
    if conn is None:
        return None
    key = f'{KEY_PREFIX}{namespace}:{_fingerprint(key_parts)}'
    try:
        *versions, stored = conn.mget([*_version_keys(tables), key])
    except redis.RedisError as error:
        _connection.mark_down(error)
        return None
    stamp = '.'.join(value or '0' for value in versions)
    if stored is None:
        return None
    built_from, _, raw = stored.partition('\n')
    if built_from != stamp:
        return None
    payload = json.loads(raw)
    logger.info('cache hit ns=%s source=redis via=load-shortcut key=%s versions=%s ms=%.1f',
                namespace, key.removeprefix(KEY_PREFIX), stamp, (time.perf_counter() - started) * 1000)
    return payload


def payload_size(payload: Any) -> int:
    """Row count for progress reporting when a load job is answered from cache."""
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for field in ('leads', 'rows', 'items'):
            if isinstance(payload.get(field), list):
                return len(payload[field])
    return 0


# --------------------------------------------------------------------- invalidation

def bump(tables: Iterable[str]) -> None:
    """Increment table versions so dependent cached payloads are skipped."""
    names = sorted(set(tables))
    if not names:
        return
    conn = client()
    if conn is None:
        return
    try:
        pipe = conn.pipeline(transaction=False)
        for name in names:
            pipe.incr(f'{VERSION_PREFIX}{name}')
        pipe.execute()
    except redis.RedisError as error:
        _connection.mark_down(error)
        return
    logger.info('cache invalidate tables=%s', ','.join(names))


def _record(session: Session, tables: Iterable[str]) -> None:
    relevant = TRACKED_TABLES.intersection(tables)
    if relevant:
        session.info.setdefault(_DIRTY_KEY, set()).update(relevant)


def _values(obj, attr: str, is_new: bool) -> list | None:
    """Every value ``attr`` held during this flush, or None when unknown.

    Reads attribute history only — never triggers a lazy load mid-flush.
    """
    state = inspect(obj)
    if attr not in state.attrs:
        return None
    history = state.attrs[attr].history
    values = [*history.added, *history.unchanged, *history.deleted]
    if values:
        return values
    if attr in state.dict:
        return [state.dict[attr]]
    if not is_new:
        return None
    # A new row that never set the attribute was inserted with the column's
    # Python-side default, or NULL when it has none.
    column = obj.__table__.c.get(attr)
    if column is None:
        return None
    if column.default is None:
        return None if column.server_default is not None else [None]
    return [column.default.arg] if column.default.is_scalar else None


def _fine_grained(table: str, obj, is_new: bool) -> set[str]:
    """The narrower keys a single row write affects (unknown → assume affected)."""
    if table == 'leads':
        values = _values(obj, 'conversation_id', is_new)
        if values is None or any(value is not None for value in values):
            return {LEADS_WITH_THREAD}
    if table == 'campaigns':
        values = _values(obj, 'status', is_new)
        if values is None or 'sent' in values:
            return {SENT_CAMPAIGNS}
    return set()


@event.listens_for(Session, 'after_flush')
def _track_flush(session: Session, flush_context) -> None:  # noqa: ARG001
    tables = set()
    new_rows = set(map(id, session.new))
    for obj in (*session.new, *session.dirty, *session.deleted):
        table = getattr(getattr(obj, '__table__', None), 'name', None)
        if table:
            tables.add(table)
            tables |= _fine_grained(table, obj, id(obj) in new_rows)
    if tables:
        _record(session, tables)


def _only_threadless_leads(statement) -> bool:
    """True when a bulk UPDATE provably touches only leads with no thread.

    That is: its WHERE requires ``conversation_id IS NULL`` and it does not set
    ``conversation_id`` itself (e.g. the draft lock in leads/campaign.attach).
    Anything less certain returns False, which keeps invalidation conservative.
    """
    try:
        if not getattr(statement, 'is_update', False):
            return False
        assigned = {getattr(column, 'key', column) for column in (statement._values or {})}
        if 'conversation_id' in assigned:
            return False
        for criterion in statement._where_criteria:
            for clause in getattr(criterion, 'clauses', [criterion]):
                left = getattr(clause, 'left', None)
                if (getattr(left, 'key', None) == 'conversation_id'
                        and getattr(getattr(clause, 'operator', None), '__name__', '') == 'is_'
                        and type(getattr(clause, 'right', None)).__name__ == 'Null'):
                    return True
    except Exception:  # noqa: BLE001 - private attributes: fall back to conservative
        return False
    return False


@event.listens_for(Session, 'do_orm_execute')
def _track_bulk(orm_execute_state) -> None:
    if not (orm_execute_state.is_update or orm_execute_state.is_delete
            or orm_execute_state.is_insert):
        return
    statement = orm_execute_state.statement
    table = getattr(statement, 'table', None)
    name = getattr(table, 'name', None)
    if not name:
        return
    # A bulk statement's rows are unknown, so it affects every finer key —
    # unless its own WHERE clause rules threaded leads out.
    finer = set()
    if name == 'leads' and not _only_threadless_leads(statement):
        finer.add(LEADS_WITH_THREAD)
    if name == 'campaigns':
        finer.add(SENT_CAMPAIGNS)
    _record(orm_execute_state.session, {name, *finer})


@event.listens_for(Session, 'after_commit')
def _invalidate_on_commit(session: Session) -> None:
    tables = session.info.pop(_DIRTY_KEY, None)
    if tables:
        bump(tables)


@event.listens_for(Session, 'after_rollback')
def _forget_on_rollback(session: Session) -> None:
    session.info.pop(_DIRTY_KEY, None)
