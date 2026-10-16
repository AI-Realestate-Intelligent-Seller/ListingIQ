import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

# Point the application at a throwaway SQLite database before app.core.config
# (and therefore app.db) is imported.
_TEST_DB = Path(tempfile.gettempdir()) / f'ListingIQ_test_{uuid.uuid4().hex}.db'
os.environ['POSTGRES_URL'] = f'sqlite:///{_TEST_DB}'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('FRONTEND_URL', 'http://localhost:3000')
# Staged lead uploads go to a throwaway directory, never the app's data folder.
_TEST_UPLOADS = Path(tempfile.gettempdir()) / f'ListingIQ_uploads_{uuid.uuid4().hex}'
os.environ['LEAD_UPLOAD_DIR'] = str(_TEST_UPLOADS)

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AiRun, Booking, Campaign, Conversation, FeatureFlag, Invitation, Lead, LeadEvent,  # noqa: E402
                        Message, OutboundSmsJob, PlatformAuditLog, User)


@pytest.fixture(scope='session', autouse=True)
def _database():
    db.Base.metadata.create_all(bind=db.engine)
    yield
    db.Base.metadata.drop_all(bind=db.engine)
    db.engine.dispose()
    _TEST_DB.unlink(missing_ok=True)
    shutil.rmtree(_TEST_UPLOADS, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_tables():
    session = db.SessionLocal()
    try:
        session.query(PlatformAuditLog).delete()
        session.query(FeatureFlag).delete()
        session.query(LeadEvent).delete()
        session.query(OutboundSmsJob).delete()
        session.query(Lead).delete()
        session.query(Message).delete()
        session.query(Conversation).delete()
        # After leads and conversations: both carry a campaign_id.
        session.query(Campaign).delete()
        session.query(Booking).delete()
        session.query(AiRun).delete()
        session.query(Invitation).delete()
        session.query(User).delete()
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture
def session():
    session = db.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    # The app's startup event runs Alembic; the schema is already created above,
    # so drive the app without lifespan handlers.
    return TestClient(app)


@pytest.fixture(autouse=True)
def sent_emails(monkeypatch):
    """Capture invitation emails instead of talking to an SMTP server."""
    outbox = []

    def fake_send(**kwargs):
        outbox.append(kwargs)

    monkeypatch.setattr('app.routes.team.send_invitation_email', fake_send)
    return outbox


@pytest.fixture
def make_user():
    def _make_user(
        email,
        role='hob',
        brokerage_id='brokerage-1',
        brokerage_name='Linchpin Global',
        password='Password123!',
        is_active=True,
    ):
        session = db.SessionLocal()
        try:
            user = User(
                email=email.lower(),
                hashed_password=hash_password(password),
                first_name='Test',
                last_name='User',
                full_name='Test User',
                brokerage_id=brokerage_id,
                brokerage_name=brokerage_name,
                role=role,
                is_head_or_owner=role == 'hob',
                is_verified=True,
                is_active=is_active,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            session.expunge(user)
            return user
        finally:
            session.close()

    return _make_user


@pytest.fixture
def auth_header(client):
    def _auth_header(email, password='Password123!'):
        response = client.post('/api/v1/auth/login', json={'email': email, 'password': password, 'full_name': None})
        assert response.status_code == 200, response.text
        return {'Authorization': f"Bearer {response.json()['access_token']}"}

    return _auth_header


def drain_outbound_queue():
    """Run the real outbound worker stages inline until no job is ready."""
    from app.sms import outbound_worker
    while (outbound_worker.process_lookup_once('test-lookup')
           or outbound_worker.process_submission_once('test-submit')):
        pass


@pytest.fixture
def drain_campaign_queue(monkeypatch):
    """Campaign Send enqueues and returns; production work happens in the worker.

    Tests written against the outcome of a send (threads, claims, reporting)
    run the real worker inline right after the enqueue, then read `started`
    and `skipped` back from the jobs, as the synchronous send used to report.
    """
    from app.leads import campaign as campaign_service

    enqueue = campaign_service.send

    def send_then_drain(session, user, campaign):
        result = enqueue(session, user, campaign)
        jobs = (session.query(OutboundSmsJob.id, OutboundSmsJob.lead_id, Lead.owner_name)
                .join(Lead, Lead.id == OutboundSmsJob.lead_id)
                .filter(OutboundSmsJob.campaign_id == result['campaign_id'])
                .order_by(OutboundSmsJob.id).all())
        reported = {item['lead_id'] for item in result['skipped']}
        drain_outbound_queue()
        session.expire_all()
        started, skipped = [], list(result['skipped'])
        for job_id, lead_id, owner_name in jobs:
            job = session.get(OutboundSmsJob, job_id)
            if job is not None and job.status == 'accepted':
                message = session.get(Message, job.message_id)
                started.append({'lead_id': lead_id, 'owner_name': owner_name,
                                'conversation_id': message.conversation_id, 'text': job.text})
            elif lead_id not in reported:
                skipped.append({'lead_id': lead_id, 'owner_name': owner_name,
                                'reason': job.last_error if job is not None else 'Not sent.'})
        return {**result, 'started': started, 'skipped': skipped}

    monkeypatch.setattr(campaign_service, 'send', send_then_drain)
