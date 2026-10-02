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
                        Message, PlatformAuditLog, User)


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
