"""Run with python -m app.propertyradar.worker. Queue state lives in the database."""

import logging
import time

from ..db import SessionLocal
from .service import Service, UnsafeOperation, initialize, operation_lock


def main():
    with SessionLocal() as db:
        initialize(db)
    while True:
        processed = False
        try:
            with SessionLocal() as db:
                service = Service(db)
                if service.row.enabled:
                    with operation_lock(db):
                        processed = service.work_once()
        except UnsafeOperation:
            pass
        except Exception:  # noqa: BLE001 -- keep worker alive without logging secrets
            # Exception bodies can contain SQL parameters; keep durable error data in the UI.
            logging.getLogger(__name__).error(
                "PropertyRadar worker iteration failed; inspect integration state"
            )
        time.sleep(0.2 if processed else 3)


if __name__ == "__main__":
    main()
