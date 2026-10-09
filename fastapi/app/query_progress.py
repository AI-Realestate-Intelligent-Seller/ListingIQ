"""Short-lived, user-scoped query progress stored in Redis.

Progress is operational state rather than application data, so Redis is a
better fit than a database table. Jobs expire automatically after five minutes.
"""

import json
from functools import lru_cache
from typing import Any
from uuid import uuid4

import redis
from fastapi.encoders import jsonable_encoder

from .core.config import settings

JOB_TTL_SECONDS = 300
KEY_PREFIX = 'listingiq:query-progress:'


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
    job = read(job_id)
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
    _write(job)


def complete(job_id: str, result: Any, *, total: int) -> None:
    job = read(job_id)
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
    _write(job)


def fail(job_id: str, error: Exception) -> None:
    job = read(job_id)
    if job is None:
        return
    job.update(status='failed', error=str(error)[:300], result=None)
    _write(job)
