"""Background lead geocoding.

The API starts this on boot (see ``start_geocoding_worker``) so every lead that
enters the pool is geocoded once and its coordinates are stored for reuse. It
can also run standalone with ``python -m app.location.worker`` from
project/fastapi.
"""

import logging
import threading
import time

from app.core.config import settings
from app.db import SessionLocal

from .queue import process_one, recover_interrupted


IDLE_POLL_SECONDS = 30.0


def main() -> None:
    logger = logging.getLogger(__name__)
    with SessionLocal() as session:
        recover_interrupted(session)

    interval = max(1.0, settings['geocoding']['request_interval_seconds'])
    while True:
        processed = False
        try:
            with SessionLocal() as session:
                processed = process_one(session)
        except Exception:  # noqa: BLE001 - one bad row must not stop the queue
            logger.exception('Lead geocoding worker iteration failed')
        # Idle checks are round trips to a remote database; new leads can wait a little.
        time.sleep(interval if processed else IDLE_POLL_SECONDS)


def start_geocoding_worker() -> threading.Thread | None:
    if not settings['geocoding']['worker_enabled']:
        return None
    thread = threading.Thread(target=main, daemon=True, name='geocoding-worker')
    thread.start()
    return thread


if __name__ == '__main__':
    main()
