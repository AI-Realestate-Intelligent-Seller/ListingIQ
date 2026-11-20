"""Small persisted scheduler for due experiment campaign variants."""

import threading
from datetime import datetime

from ..db import SessionLocal
from ..leads import campaign as campaign_service
from ..logger import get_logger
from ..models import Campaign, Experiment, User

logger = get_logger(__name__)
_stop = threading.Event()
_worker = None


def launch_experiment(experiment_id: int, now: datetime | None = None) -> bool:
    """Launch one scheduled experiment immediately and persist its outcome."""
    now = now or datetime.utcnow()
    session = SessionLocal()
    try:
        experiment = (session.query(Experiment).filter(
            Experiment.id == experiment_id, Experiment.status == 'scheduled',
        ).with_for_update().first())
        if experiment is None:
            return False
        experiment.status = 'launching'
        session.commit()
        user = session.get(User, experiment.user_id)
        campaigns = (session.query(Campaign)
                     .filter(Campaign.experiment_id == experiment.id)
                     .order_by(Campaign.id).all())
        if user is None or not campaigns:
            raise ValueError('Experiment owner or variants are unavailable.')
        for campaign in campaigns:
            campaign_service.send(session, user, campaign)
        experiment = session.get(Experiment, experiment_id)
        experiment.status = 'running'
        experiment.launched_at = now
        experiment.error = None
        session.commit()
        return True
    except Exception as error:  # noqa: BLE001
        session.rollback()
        experiment = session.get(Experiment, experiment_id)
        if experiment:
            experiment.status = 'failed'
            experiment.error = str(error)[:2000]
            session.commit()
        logger.exception('Experiment %s failed to launch: %s', experiment_id, error)
        return False
    finally:
        session.close()


def run_due(now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    session = SessionLocal()
    try:
        ids = [row[0] for row in session.query(Experiment.id).filter(
            Experiment.status == 'scheduled', Experiment.scheduled_at <= now,
        ).order_by(Experiment.scheduled_at, Experiment.id).all()]
    finally:
        session.close()
    launched = 0
    for experiment_id in ids:
        if launch_experiment(experiment_id, now):
            launched += 1
    return launched


def _run():
    while not _stop.wait(15):
        try:
            run_due()
        except Exception:  # keep the scheduler alive after an infrastructure error
            logger.exception('Experiment scheduler tick failed.')


def start_scheduler():
    global _worker
    if _worker and _worker.is_alive():
        return
    _stop.clear()
    _worker = threading.Thread(target=_run, name='experiment-scheduler', daemon=True)
    _worker.start()


def stop_scheduler():
    _stop.set()
