"""Short-lived, user-scoped query progress stored in Redis.

Progress is operational state rather than application data, so Redis is a
better fit than a database table. Jobs expire automatically after five minutes.
"""

import json
import threading
import time
from functools import lru_cache
from typing import Any
from uuid import uuid4

import redis
from fastapi.encoders import jsonable_encoder

from .core.config import settings

JOB_TTL_SECONDS = 300
KEY_PREFIX = 'listingiq:query-progress:'

# Progress is reported once per row, but a person can only see a few frames a
# second. Each Redis write is a network round trip, so an intermediate update is
# written only after PROGRESS_MIN_INTERVAL seconds of real work since the
# previous write *finished* (a slow Redis can never make writes back-to-back).
# The first, last and completion states are always written.
PROGRESS_MIN_INTERVAL = 0.25

# Jobs run in the process that created them (FastAPI background tasks), so the
# job document is kept here and each write is a single SET with no read first.
_local_jobs: dict[str, dict[str, Any]] = {}
_last_written: dict[str, tuple[float, int]] = {}
_lock = threading.Lock()


class ProgressStoreUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _client():
    return redis.Redis.from_url(
        settings['redis_url'],
        socket_connect_timeout=2,
        socket_timeout=3,
        decode_responses=True,
    )


def _key(job_id: str) -> str:
    return f'{KEY_PREFIX}{job_id}'


def _write(job: dict[str, Any]) -> None:
    try:
        _client().set(_key(job['job_id']), json.dumps(jsonable_encoder(job)), ex=JOB_TTL_SECONDS)
    except redis.RedisError as error:
        raise ProgressStoreUnavailable('Query progress storage is unavailable.') from error


def _working_copy(job_id: str) -> dict[str, Any] | None:
    """This process's copy of the job, or the stored one if another process made it."""
    with _lock:
        job = _local_jobs.get(job_id)
    if job is not None:
        return job
    job = read(job_id)
    if job is not None:
        with _lock:
            _local_jobs[job_id] = job
    return job


def _forget(job_id: str) -> None:
    with _lock:
        _local_jobs.pop(job_id, None)
        _last_written.pop(job_id, None)


def create(user_id: int, operation: str) -> dict[str, Any]:
    job = {
        'job_id': uuid4().hex,
        'user_id': user_id,
        'operation': operation,
        'status': 'queued',
        'total': 0,
        'loaded': 0,
        'remaining': 0,
        'percent': 0,
        'result': None,
        'error': None,
    }
    _write(job)
    with _lock:
        _local_jobs[job['job_id']] = dict(job)
    return job


def read(job_id: str) -> dict[str, Any] | None:
    try:
        raw = _client().get(_key(job_id))
    except redis.RedisError as error:
        raise ProgressStoreUnavailable('Query progress storage is unavailable.') from error
    if not raw:
        return None
    return json.loads(raw)


def update(job_id: str, *, total: int, loaded: int, status: str = 'processing') -> None:
    job = _working_copy(job_id)
    if job is None:
        return
    safe_total = max(int(total), 0)
    safe_loaded = min(max(int(loaded), 0), safe_total) if safe_total else 0
    job.update(
        status=status,
        total=safe_total,
        loaded=safe_loaded,
        remaining=max(safe_total - safe_loaded, 0),
        percent=round((safe_loaded / safe_total) * 100) if safe_total else 0,
    )
    with _lock:
        last = _last_written.get(job_id)
    boundary = safe_loaded == 0 or safe_loaded >= safe_total
    if last is not None and not boundary and time.monotonic() - last[0] < PROGRESS_MIN_INTERVAL:
        return  # kept in memory; the next written frame carries it
    _write(job)
    with _lock:
        _last_written[job_id] = (time.monotonic(), job['percent'])


def complete(job_id: str, result: Any, *, total: int) -> None:
    job = _working_copy(job_id)
    if job is None:
        return
    safe_total = max(int(total), 0)
    job.update(
        status='completed',
        total=safe_total,
        loaded=safe_total,
        remaining=0,
        percent=100,
        result=result,
        error=None,
    )
    try:
        _write(job)
    finally:
        _forget(job_id)


def fail(job_id: str, error: Exception) -> None:
    job = _working_copy(job_id)
    if job is None:
        return
    job.update(status='failed', error=str(error)[:300], result=None)
    try:
        _write(job)
    finally:
        _forget(job_id)
