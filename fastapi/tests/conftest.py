import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

# Test-only safety: no test may send a real SMS or reach a paid AI provider.
# These run before app.core.config loads .env, and load_dotenv never overrides
# a variable that is already set, so local credentials are never picked up.
os.environ['SMS_MODE'] = 'simulation'
os.environ['SIMULATION_SMS_URL'] = ''
os.environ['AI_PROVIDER'] = 'deepseek'
for _secret in ('DEEPSEEK_API_KEY', 'AI_API_KEY', 'ANTHROPIC_API_KEY', 'TELNYX_API_KEY'):
    os.environ[_secret] = ''

# Keep the remaining production credentials out of the test process too. Only
# variable names are matched here; no value from .env is read. The integration
# tests set the secrets they need themselves.
import re  # noqa: E402

_CREDENTIAL_NAME = re.compile(
    r'(_API_KEY|_API_TOKEN|_SECRET|_PASSWORD|_ACCESS_KEY_ID|_SECRET_ACCESS_KEY'
    r'|_PRIVATE_KEY|_ENCRYPTION_KEY)$')
_dotenv = BASE_DIR / '.env'
_dotenv_names = set(re.findall(r'(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=',
                               _dotenv.read_text(encoding='utf-8'))) if _dotenv.exists() else set()
for _name in _dotenv_names:
    if _CREDENTIAL_NAME.search(_name):
        os.environ[_name] = ''
from cryptography.fernet import Fernet  # noqa: E402

os.environ['FERNET_KEY'] = Fernet.generate_key().decode()   # throwaway, valid format
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['REDIS_URL'] = 'redis://127.0.0.1:6379'          # credential-free; connections are refused
os.environ['QDRANT_URL'] = ''
os.environ['QDRANT_API_KEY'] = ''

# Refuse every network connection (loopback included), so a test that forgets to
# mock a provider fails instead of calling it. Unix sockets stay available.
import socket  # noqa: E402

BLOCKED_CONNECTIONS: list[str] = []
BLOCKED_LOOKUPS: list[str] = []
_LOCAL_NAMES = {None, '', 'localhost', '127.0.0.1', '::1'}
_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_sendto = socket.socket.sendto
_original_sendmsg = socket.socket.sendmsg
_original_getaddrinfo = socket.getaddrinfo
_original_gethostbyname = socket.gethostbyname
_original_gethostbyname_ex = socket.gethostbyname_ex


def _refuse(sock, address):
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0] if isinstance(address, tuple) else str(address)
        BLOCKED_CONNECTIONS.append(host)
        raise ConnectionRefusedError(f'Network is disabled during tests: {host}')
    return None


def _refuse_lookup(host):
    if host not in _LOCAL_NAMES:
        BLOCKED_LOOKUPS.append(str(host))
        raise socket.gaierror(socket.EAI_NONAME, 'DNS lookups are disabled during tests')


def _guarded_connect(sock, address):
    _refuse(sock, address)
    return _original_connect(sock, address)


def _guarded_connect_ex(sock, address):
    _refuse(sock, address)
    return _original_connect_ex(sock, address)


def _guarded_sendto(sock, data, *args):
    # UDP covers DNS resolvers that bypass getaddrinfo.
    _refuse(sock, args[-1])
    return _original_sendto(sock, data, *args)


def _guarded_sendmsg(sock, buffers, *args):
    if len(args) >= 3 and args[2] is not None:
        _refuse(sock, args[2])
    return _original_sendmsg(sock, buffers, *args)


def _guarded_getaddrinfo(host, *args, **kwargs):
    _refuse_lookup(host)
    return _original_getaddrinfo(host, *args, **kwargs)


def _guarded_gethostbyname(host):
    _refuse_lookup(host)
    return _original_gethostbyname(host)


def _guarded_gethostbyname_ex(host):
    _refuse_lookup(host)
    return _original_gethostbyname_ex(host)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.socket.sendto = _guarded_sendto
socket.socket.sendmsg = _guarded_sendmsg
socket.getaddrinfo = _guarded_getaddrinfo
socket.gethostbyname = _guarded_gethostbyname
socket.gethostbyname_ex = _guarded_gethostbyname_ex


def pytest_terminal_summary(terminalreporter):
    terminalreporter.write_line(
        f'network guard: {len(BLOCKED_CONNECTIONS)} blocked connections, '
        f'{len(BLOCKED_LOOKUPS)} blocked DNS lookups')

# Point the application at a throwaway SQLite database before app.core.config
# (and therefore app.db) is imported.
_TEST_DB = Path(tempfile.gettempdir()) / f'ListingIQ_test_{uuid.uuid4().hex}.db'
os.environ['POSTGRES_URL'] = f'sqlite:///{_TEST_DB}'
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ.setdefault('FRONTEND_URL', 'http://localhost:3000')
# Staged lead uploads go to a throwaway directory, never the app's data folder.
_TEST_UPLOADS = Path(tempfile.gettempdir()) / f'ListingIQ_uploads_{uuid.uuid4().hex}'
os.environ['LEAD_UPLOAD_DIR'] = str(_TEST_UPLOADS)
_TEST_PERSONA_STORAGE = Path(tempfile.gettempdir()) / f'ListingIQ_personas_{uuid.uuid4().hex}'
os.environ['PERSONA_STORAGE_BACKEND'] = 'local'
os.environ['PERSONA_STORAGE_ROOT'] = str(_TEST_PERSONA_STORAGE)
os.environ['PERSONA_INDEX_WORKER_ENABLED'] = 'false'

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (AiRun, Booking, Campaign, Conversation, FeatureFlag, InboundSmsEvent, Invitation, Lead,  # noqa: E402
                        LeadEvent, Message, OutboundSmsJob, PlatformAuditLog, User)
from app.personas.models import (Persona, PersonaDefault, PersonaDraft, PersonaEvent,  # noqa: E402
                                 PersonaDraftExample, PersonaSubmission,
                                 PersonaSubmissionExample, PersonaVersion,
                                 PersonaVersionExample, PersonaImportJob,
                                 PersonaKnowledgeIndexJob)


@pytest.fixture(scope='session', autouse=True)
def _database():
    db.Base.metadata.create_all(bind=db.engine)
    yield
    db.Base.metadata.drop_all(bind=db.engine)
    db.engine.dispose()
    _TEST_DB.unlink(missing_ok=True)
    shutil.rmtree(_TEST_UPLOADS, ignore_errors=True)
    shutil.rmtree(_TEST_PERSONA_STORAGE, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_tables():
    session = db.SessionLocal()
    try:
        session.query(PersonaEvent).delete()
        session.query(PersonaKnowledgeIndexJob).delete()
        session.query(PersonaDefault).delete()
        session.query(PersonaImportJob).delete()
        session.query(PersonaDraftExample).delete()
        session.query(PersonaSubmissionExample).delete()
        session.query(PersonaVersionExample).delete()
        session.query(PersonaDraft).delete()
        session.query(PersonaSubmission).delete()
        # Break the intentional active-version cycle before deleting immutable
        # versions and their stable Persona roots.
        session.query(Persona).update({Persona.active_version_id: None}, synchronize_session=False)
        session.query(PersonaVersion).delete()
        session.query(Persona).delete()
        session.query(PlatformAuditLog).delete()
        session.query(FeatureFlag).delete()
        session.query(LeadEvent).delete()
        session.query(OutboundSmsJob).delete()
        session.query(Lead).delete()
        session.query(InboundSmsEvent).delete()
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
    # so drive the app without lifespan handlers. This environment's prerelease
    # CPython 3.11 asyncio loop cannot wake a loop running in TestClient's portal
    # thread; uvloop (already installed by uvicorn[standard]) provides the same
    # ASGI semantics without that interpreter-level cross-thread deadlock.
    return TestClient(app, backend_options={'use_uvloop': True})


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
