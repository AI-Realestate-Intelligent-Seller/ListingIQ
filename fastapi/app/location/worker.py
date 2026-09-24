"""Run with ``python -m app.location.worker`` from project/fastapi."""

import logging
import time

from app.core.config import settings
from app.db import SessionLocal

from .queue import process_one, recover_interrupted


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
        time.sleep(interval if processed else 3.0)


if __name__ == '__main__':
    main()
