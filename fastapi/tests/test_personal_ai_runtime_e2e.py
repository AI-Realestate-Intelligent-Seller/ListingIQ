"""Personal AI runtime end to end, offline.

Covers trusted identity, user-scoped retrieval on real local vector engines
(in-memory Qdrant and on-disk Chroma), dynamic Persona prompts, original
document storage, durable knowledge indexing and the bounded reviewer.

No AI provider, embedding API, R2 endpoint or SMS gateway is reached: model
calls are scripted per feature, embeddings are a deterministic local hash and
object storage is a fake S3 client. Passing proves the wiring and the
isolation rules, not how a live model writes.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests

from app import db
from app.ai import client as ai_client
from app.models import Conversation, Message, User
from app.personas import imports, knowledge_indexing, storage as persona_storage
from app.personas.models import (
    Persona, PersonaEvent, PersonaImportJob, PersonaKnowledgeIndexJob, PersonaVersion,
)
from app.sms import bobbie, knowledge, reply_suggest, service
from app.sms.prompt import build_agent_prompt
from app.sms.runtime_context import resolve_agent_runtime_context
from app import vector_store as vector_store_module
from app.vector_store import ChromaVectorStore, QdrantVectorStore
from app.core.config import settings

API = "/api/v1/personas"
FIXTURE = Path(__file__).parent / "fixtures" / "persona" / "michael-kang.md"
DOCUMENT = FIXTURE.read_text(encoding="utf-8")
KOREAN_OUTREACH = ("안녕하세요 {FirstName} 사장님, SpaceMatch의 마이클 강입니다. 시카고에서 세탁소·비즈니스 매매를 많이 진행하고 "
                   "있습니다. 혹시 은퇴나 매각 생각 있으시면 편하게 상담 한번 드리겠습니다.")
COLLECTION = settings["sms"]["agent_knowledge_collection"]


# ---------------------------------------------------------------------------
# Offline guards and fakes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Refuse every provider/network path; tests opt into scripted model calls."""
    attempts = []

    def refuse(name):
        def _refuse(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"{name} must not be called")
        return _refuse

    monkeypatch.delenv("PERSONA_AI_IMPORT_ENABLED", raising=False)
    monkeypatch.setattr(requests, "post", refuse("requests.post"))
    monkeypatch.setattr(requests, "get", refuse("requests.get"))
    monkeypatch.setattr(ai_client, "completion", refuse("ai.client.completion"))
    monkeypatch.setattr(bobbie, "completion", refuse("bobbie.completion"))
    monkeypatch.setattr(imports, "extract_document", refuse("imports.extract_document"))
    yield attempts
    assert attempts == []


class HashEmbedding:
    """Deterministic local embedding: no model download, no network."""

    name = "offline-hash-embedding"
    is_semantic = False

    def embed_one(self, text):
        digest = hashlib.sha256(str(text).encode("utf-8")).digest()
        return [((byte / 255.0) - 0.5) for byte in digest[:16]]

    def embed(self, texts):
        return [self.embed_one(text) for text in texts]


@pytest.fixture
def qdrant_store(monkeypatch):
    qdrant_client = pytest.importorskip("qdrant_client")
    monkeypatch.setattr(vector_store_module, "_local_embedding_provider", lambda: HashEmbedding())
    store = object.__new__(QdrantVectorStore)
    store.url = ":memory:"
    store.api_key = ""
    store.timeout = 10.0
    store.cloud_inference = False
    store.embedding_model = "offline"
    store.vector_dimension = 16
    store.path = None
    store.client = qdrant_client.QdrantClient(":memory:")
    store._pending_collection_metadata = {}
    store.backend = "qdrant"
    monkeypatch.setattr(knowledge, "vector_store", store)
    monkeypatch.setattr(knowledge_indexing, "vector_store", store)
    return store


@pytest.fixture
def chroma_store(monkeypatch, tmp_path):
    pytest.importorskip("chromadb")
    monkeypatch.setattr(vector_store_module, "_local_embedding_provider", lambda: HashEmbedding())
    store = ChromaVectorStore(path=str(tmp_path / "chroma"))
    if not store.available:
        pytest.skip("chromadb could not open a local store")
    monkeypatch.setattr(knowledge, "vector_store", store)
    monkeypatch.setattr(knowledge_indexing, "vector_store", store)
    return store


class FakeS3:
    """The subset of the S3 API R2PersonaStorage uses."""

    def __init__(self, fail_put=False):
        self.objects = {}
        self.fail_put = fail_put

    def put_object(self, Bucket, Key, Body, ContentType):
        if self.fail_put:
            raise ConnectionError("R2 unavailable")
        self.objects[(Bucket, Key)] = bytes(Body)

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            error = RuntimeError("missing")
            error.response = {"Error": {"Code": "NoSuchKey"}}
            raise error

        class Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data
        return {"Body": Body(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture
def r2(monkeypatch):
    s3 = FakeS3()
    monkeypatch.setenv("PERSONA_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("CLOUDFLARE_R2_BUCKET", "private-personas")
    for name in ("CLOUDFLARE_R2_ENDPOINT", "CLOUDFLARE_R2_ACCESS_KEY_ID", "CLOUDFLARE_R2_SECRET_ACCESS_KEY",
                 "PERSONA_STORAGE_DB_COPY", "PERSONA_R2_PREFIX", "PERSONA_R2_BUCKET", "PERSONA_R2_ENDPOINT",
                 "PERSONA_R2_CREDENTIALS_FILE", "PERSONA_R2_ACCESS_KEY_ID", "PERSONA_R2_SECRET_ACCESS_KEY"):
        monkeypatch.delenv(name, raising=False)
    original = persona_storage.R2PersonaStorage

    class TestR2(original):
        def __init__(self, client=None):
            super().__init__(client=client or s3)

    monkeypatch.setattr(persona_storage, "R2PersonaStorage", TestR2)
    return s3


def scripted(monkeypatch, script):
    """Route bobbie/reply-suggest model calls by feature to scripted replies."""
    calls = []

    def fake(messages, **options):
        feature = options.get("feature", "")
        calls.append({"feature": feature, "messages": messages, "options": options})
        queue = script.get(feature) or script.get(feature.split(".")[0] + ".*")
        if not queue:
            raise AssertionError(f"unexpected model call for {feature}")
        reply = queue.pop(0)
        if callable(reply):
            reply = reply()
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, dict) else {"role": "assistant", "content": reply}

    monkeypatch.setattr(bobbie, "completion", fake)
    monkeypatch.setattr(ai_client, "completion", fake)
    return calls


# ---------------------------------------------------------------------------
# Persona helpers (real API flows)
# ---------------------------------------------------------------------------

def _publish_manual(client, make_user, auth_header, email, brokerage, name, brokerage_name="Linchpin Global",
                    example=None, biography=None):
    user = make_user(email, role="agent", brokerage_id=brokerage, brokerage_name=brokerage_name)
    headers = auth_header(email)
    persona = client.post(API, headers=headers, json={"full_name": name}).json()
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{persona['id']}/draft", headers=headers, json={
        "revision": draft["revision"], "professional_title": "Broker",
        "biography": biography or f"{name} biography", "specialties": [f"{name} specialty"],
        "service_areas": [f"{name} area"],
        "profile_config": {"identity": {"license_number": f"TEST-{user.id}",
                                        "licensing_jurisdiction": "Test State", "licensing_status": "Active"}},
    }).json()
    examples = client.put(f"{API}/{persona['id']}/examples", headers=headers, json={
        "revision": saved["revision"],
        "examples": [example or {"scenario": "Greeting", "channel": "sms", "language": "en",
                                 "customer_message": "Hello", "preferred_response": f"Hi from {name}.",
                                 "provenance": "user_written"}],
    }).json()
    version = client.post(f"{API}/{persona['id']}/versions", headers=headers,
                          json={"revision": examples["revision"]})
    assert version.status_code in (200, 201), version.text
    return user, headers, persona, version.json()


def _republish(client, headers, persona_id, **changes):
    draft = client.get(f"{API}/{persona_id}/draft", headers=headers).json()
    saved = client.patch(f"{API}/{persona_id}/draft", headers=headers, json={"revision": draft["revision"], **changes})
    assert saved.status_code == 200, saved.text
    version = client.post(f"{API}/{persona_id}/versions", headers=headers, json={"revision": saved.json()["revision"]})
    assert version.status_code in (200, 201), version.text
    return version.json()


def _import_owner(client, make_user, auth_header, email, brokerage="brokerage-1", name="Michael Kang"):
    user = make_user(email, role="broker", brokerage_id=brokerage)
    headers = auth_header(email)
    persona = client.post(API, headers=headers, json={"full_name": name}).json()
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("michael-kang.md", DOCUMENT.encode(), "text/markdown")})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        assert imports.process_one(session) is True
    job = client.get(f"{API}/{persona['id']}/imports/{uploaded.json()['id']}", headers=headers).json()
    assert job["status"] == "completed", job
    applied = client.post(f"{API}/{persona['id']}/imports/{job['id']}/apply", headers=headers, json={"revision": 1})
    assert applied.status_code == 200, applied.text
    return user, headers, persona, job, applied.json()


def _review_and_publish(client, headers, persona_id, *, confirm=(), dispute=(), keep=(), dismiss=()):
    """Owner decisions on imported claims/details, then self-publish."""
    draft = client.get(f"{API}/{persona_id}/draft", headers=headers).json()
    profile = draft["profile_config"]
    profile["confirmations"].update({"license_number": "not_applicable", "licensing_status": "not_applicable"})
    profile["na_reasons"].update({"license_number": "Added after lookup", "licensing_status": "Confirming"})
    review = profile["source_review"]
    # Imports arrive confirmed and kept; these tests narrow the set to the
    # listed indexes, so everything else is disputed or dismissed.
    for index, claim in enumerate(review["claims"]):
        claim["owner_status"] = "owner_confirmed" if index in confirm else "owner_disputed"
    for index, detail in enumerate(review["unmapped_details"]):
        detail["owner_status"] = "kept" if index in keep else "dismissed"
    saved = client.patch(f"{API}/{persona_id}/draft", headers=headers, json={
        "revision": draft["revision"], "profile_config": {
            "confirmations": profile["confirmations"], "na_reasons": profile["na_reasons"],
            "source_review": review}})
    assert saved.status_code == 200, saved.text
    version = client.post(f"{API}/{persona_id}/versions", headers=headers, json={"revision": saved.json()["revision"]})
    assert version.status_code in (200, 201), version.text
    return review, version.json()


def _conversation(session, user_id, contact="+13125550100", history=()):
    conversation = Conversation(contact=contact, user_id=user_id, name="Owner", property_address="1 Test St",
                                ai_enabled=True, handled_by="bobbie", lead_status="processing")
    session.add(conversation)
    session.commit()
    start = datetime.utcnow() - timedelta(minutes=30)
    for minute, (direction, text) in enumerate(history):
        session.add(Message(conversation_id=conversation.id, direction=direction,
                            event_type="message.received" if direction == "inbound" else "ai.reply", text=text,
                            status="received" if direction == "inbound" else "delivered",
                            created_at=start + timedelta(minutes=minute)))
    session.commit()
    session.refresh(conversation)
    return conversation


def _context(user_id):
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, contact=f"+1312555{user_id:04d}")
        return resolve_agent_runtime_context(session, conversation)


def _index_all(store=None):
    processed = 0
    with db.SessionLocal() as session:
        while knowledge_indexing.process_one(session, store):
            processed += 1
    return processed


def _job(version_id):
    with db.SessionLocal() as session:
        return session.query(PersonaKnowledgeIndexJob).filter_by(version_id=version_id).one_or_none()


# ---------------------------------------------------------------------------
# Workstream 3: original document storage
# ---------------------------------------------------------------------------

def test_local_storage_keeps_original_and_database_copy_without_r2_credentials(client, make_user, auth_header):
    user = make_user("store-local@example.com", role="broker")
    headers = auth_header("store-local@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Local Owner"}).json()
    content = DOCUMENT.encode()
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("bio.md", content, "text/markdown")})
    assert uploaded.status_code == 202, uploaded.text
    with db.SessionLocal() as session:
        job = session.get(PersonaImportJob, uploaded.json()["id"])
        assert job.storage_backend == "local"
        assert job.storage_key == (f"personas/brokerage-brokerage-1/user-{user.id}/imports/{job.id}/original.md")
        assert bytes(job.document_bytes) == content  # local disk is never the only copy
        assert persona_storage.LocalPersonaStorage().read(job.storage_key) == content
        assert persona_storage.read_original(job) == content
        assert job.file_hash == hashlib.sha256(content).hexdigest() and job.file_size == len(content)
    assert "storage_key" not in uploaded.text and "personas/brokerage" not in uploaded.text


def test_r2_round_trip_stores_only_in_object_storage_and_parses_again(client, make_user, auth_header, r2):
    user = make_user("store-r2@example.com", role="broker", brokerage_id="b-r2")
    headers = auth_header("store-r2@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Michael Kang"}).json()
    content = DOCUMENT.encode()
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("michael-kang.md", content, "text/markdown")})
    assert uploaded.status_code == 202, uploaded.text
    job_id = uploaded.json()["id"]
    key = f"personas/brokerage-b-r2/user-{user.id}/imports/{job_id}/original.md"
    assert r2.objects == {("private-personas", key): content}
    with db.SessionLocal() as session:
        job = session.get(PersonaImportJob, job_id)
        assert job.storage_backend == "r2" and job.storage_key == key and job.document_bytes is None
        assert imports.process_one(session) is True
        session.refresh(job)
        assert job.status == "completed"
        assert KOREAN_OUTREACH in json.dumps(job.proposal, ensure_ascii=False)
        # Retry/resume re-reads the same verified bytes from R2.
        assert persona_storage.read_original(job) == content


def test_failed_r2_write_creates_no_import_job(client, make_user, auth_header, r2):
    make_user("store-fail@example.com", role="broker")
    headers = auth_header("store-fail@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Fail Owner"}).json()
    r2.fail_put = True
    uploaded = client.post(f"{API}/{persona['id']}/imports", headers=headers,
                           files={"file": ("bio.md", b"# Bio", "text/markdown")})
    assert uploaded.status_code == 503
    assert uploaded.json()["detail"]["code"] == "storage_unavailable"
    with db.SessionLocal() as session:
        assert session.query(PersonaImportJob).count() == 0


def test_object_is_removed_when_the_job_row_cannot_commit(client, make_user, auth_header, r2):
    owner = make_user("store-orphan@example.com", role="broker")
    headers = auth_header("store-orphan@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Orphan Owner"}).json()
    with db.SessionLocal() as session:
        actor = session.get(User, owner.id)
        real_commit = session.commit

        def failing_commit():
            session.commit = real_commit
            raise RuntimeError("database unavailable")

        session.commit = failing_commit
        with pytest.raises(RuntimeError):
            imports.create_job(session, actor, persona["id"], "bio.md", "text/markdown", b"# Bio")
        assert session.query(PersonaImportJob).count() == 0
    assert r2.objects == {}


def test_legacy_database_byte_imports_remain_readable(client, make_user, auth_header):
    user = make_user("store-legacy@example.com", role="broker")
    headers = auth_header("store-legacy@example.com")
    persona = client.post(API, headers=headers, json={"full_name": "Michael Kang"}).json()
    content = DOCUMENT.encode()
    with db.SessionLocal() as session:
        row = session.get(Persona, persona["id"])
        job = PersonaImportJob(
            id="legacy" + "0" * 26, persona_id=row.id, brokerage_id=row.brokerage_id, owner_user_id=user.id,
            base_revision=1, file_name="michael-kang.md", media_type="text/markdown", file_size=len(content),
            file_hash=hashlib.sha256(content).hexdigest(), document_bytes=content, storage_backend="database",
            storage_key=None, status="queued", stages=imports._stage_map(), retryable=0, attempt_count=0,
            extraction_mode="local", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        session.add(job)
        session.commit()
        assert imports.process_one(session) is True
        session.refresh(job)
        assert job.status == "completed" and persona_storage.read_original(job) == content


def test_checksum_mismatch_falls_back_to_verified_database_copy(make_user, tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_STORAGE_ROOT", str(tmp_path))
    content = b"original bytes"

    class Job:
        id = "j1"
        brokerage_id = "b1"
        owner_user_id = 7
        storage_backend = "local"
        storage_key = "personas/brokerage-b1/user-7/imports/j1/original.txt"
        file_hash = hashlib.sha256(content).hexdigest()
        document_bytes = content

    persona_storage.LocalPersonaStorage().write(Job.storage_key, b"tampered bytes")
    assert persona_storage.read_original(Job) == content
    Job.document_bytes = None
    with pytest.raises(persona_storage.PersonaStorageError):
        persona_storage.read_original(Job)


def test_cross_user_source_access_is_denied(client, make_user, auth_header):
    make_user("store-a@example.com", role="broker")
    make_user("store-b@example.com", role="broker")
    a, b = auth_header("store-a@example.com"), auth_header("store-b@example.com")
    persona = client.post(API, headers=a, json={"full_name": "Owner A"}).json()
    job = client.post(f"{API}/{persona['id']}/imports", headers=a,
                      files={"file": ("bio.md", b"# A only", "text/markdown")}).json()
    assert client.get(f"{API}/{persona['id']}/imports/{job['id']}", headers=b).status_code in (403, 404)

    class Forged:
        id = job["id"]
        brokerage_id = "brokerage-1"
        owner_user_id = 999_999  # row claims another owner than the object key encodes
        storage_backend = "local"
        file_hash = None
        document_bytes = None

    with db.SessionLocal() as session:
        Forged.storage_key = session.get(PersonaImportJob, job["id"]).storage_key
    with pytest.raises(persona_storage.PersonaStorageError):
        persona_storage.read_original(Forged)


# ---------------------------------------------------------------------------
# Workstream 4: durable indexing
# ---------------------------------------------------------------------------

def test_publication_queues_only_eligible_owner_reviewed_knowledge_while_store_is_offline(
        client, make_user, auth_header, monkeypatch):
    class Offline:
        def __getattr__(self, name):
            raise AssertionError(f"publication must not touch the vector store ({name})")

    monkeypatch.setattr(knowledge_indexing, "vector_store", Offline())
    monkeypatch.setattr(knowledge, "vector_store", Offline())
    user, headers, persona, job, _draft = _import_owner(client, make_user, auth_header, "ix-queue@example.com")
    review, version = _review_and_publish(client, headers, persona["id"], confirm=(0, 1), dispute=(2,),
                                          keep=(0,), dismiss=(1,))
    queued = _job(version["id"])
    assert queued is not None and queued.status == "pending" and queued.attempt_count == 0
    assert queued.owner_user_id == user.id and queued.brokerage_id == "brokerage-1"
    assert queued.chunk_count == 3  # two confirmed claims + one kept detail
    with db.SessionLocal() as session:
        texts = [chunk["text"] for chunk in knowledge_indexing.eligible_chunks(session.get(PersonaVersion, version["id"]))]
    statements = [claim["statement"] for claim in review["claims"]]
    assert any(statements[0] in text for text in texts) and any(statements[1] in text for text in texts)
    assert not any(statements[2] in text for text in texts)           # disputed
    assert not any(statement in text for statement in statements[3:] for text in texts)  # not selected
    details = [detail["detail"] for detail in review["unmapped_details"]]
    assert any(details[0] in text for text in texts) and not any(details[1] in text for text in texts)
    # Illustrative examples and style never become retrievable facts.
    assert not any("사장님" in text for text in texts)


def test_publication_without_eligible_knowledge_creates_no_job(client, make_user, auth_header):
    _user, _headers, persona, version = _publish_manual(
        client, make_user, auth_header, "ix-none@example.com", "brokerage-1", "Plain Agent")
    assert _job(version["id"]) is None
    with db.SessionLocal() as session:
        assert session.query(PersonaEvent).filter_by(
            persona_version_id=version["id"], event_type="persona.indexing_not_required").count() == 1


def test_worker_indexes_with_trusted_payload_retries_and_is_idempotent(
        client, make_user, auth_header, qdrant_store, monkeypatch):
    user, headers, persona, job, _draft = _import_owner(client, make_user, auth_header, "ix-run@example.com")
    _review, version = _review_and_publish(client, headers, persona["id"], confirm=(0, 1))

    requested_indexes = []
    real_index = qdrant_store.client.create_payload_index
    monkeypatch.setattr(qdrant_store.client, "create_payload_index",
                        lambda **kw: requested_indexes.append((kw["field_name"], str(kw["field_schema"])))
                        or real_index(**kw))
    real_add = qdrant_store.add_texts
    monkeypatch.setattr(qdrant_store, "add_texts", lambda *a, **k: False)
    assert _index_all() == 1
    failed = _job(version["id"])
    assert failed.status == "failed" and failed.attempt_count == 1 and failed.error_message
    assert failed.next_attempt_at > datetime.utcnow()
    assert _index_all() == 0  # backoff respected

    monkeypatch.setattr(qdrant_store, "add_texts", real_add)
    with db.SessionLocal() as session:
        session.get(PersonaKnowledgeIndexJob, failed.id).next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    assert _index_all() == 1
    ready = _job(version["id"])
    assert ready.status == "ready" and ready.attempt_count == 2 and ready.completed_at
    assert qdrant_store.count(COLLECTION) == 2

    points = qdrant_store.client.scroll(COLLECTION, limit=10, with_payload=True)[0]
    for point in points:
        meta = point.payload["metadata"]
        assert meta["owner_user_id"] == user.id and meta["brokerage_id"] == "brokerage-1"
        assert meta["published_version_id"] == version["id"] and meta["approval_status"] == "eligible"
        assert meta["source_document_id"] == job["id"] and meta["source_type"] == "agent_knowledge"
        assert meta["content_hash"] == hashlib.sha256(meta["text"].encode()).hexdigest()
        assert meta["embedding_model"] == "offline-hash-embedding" and isinstance(meta["chunk_index"], int)
    # Local Qdrant ignores payload indexes, so assert what was requested.
    assert {name for name, _schema in requested_indexes} == {
        f"metadata.{name}" for name in knowledge_indexing.PAYLOAD_INDEXES}

    # Re-running the same job upserts the same deterministic points.
    with db.SessionLocal() as session:
        row = session.get(PersonaKnowledgeIndexJob, ready.id)
        row.status, row.attempt_count = "pending", 0
        session.commit()
    assert _index_all() == 1
    assert qdrant_store.count(COLLECTION) == 2


def test_exhausted_job_stops_and_reactivation_grants_fresh_attempts(client, make_user, auth_header, qdrant_store,
                                                                    monkeypatch):
    _user, headers, persona, _job_row, _draft = _import_owner(client, make_user, auth_header, "ix-exhaust@example.com")
    _review, version = _review_and_publish(client, headers, persona["id"], confirm=(0,))
    monkeypatch.setattr(qdrant_store, "add_texts", lambda *a, **k: False)
    for _ in range(3):
        with db.SessionLocal() as session:
            row = session.query(PersonaKnowledgeIndexJob).one()
            row.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
            session.commit()
        _index_all()
    exhausted = _job(version["id"])
    assert exhausted.status == "failed" and exhausted.attempt_count == 3
    with db.SessionLocal() as session:
        session.get(PersonaKnowledgeIndexJob, exhausted.id).next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        session.commit()
    assert _index_all() == 0  # terminal until re-activated
    activated = client.post(f"{API}/{persona['id']}/versions/{version['version_number']}/activate", headers=headers)
    assert activated.status_code == 200, activated.text
    again = _job(version["id"])
    assert again.status == "pending" and again.attempt_count == 0


def test_interrupted_worker_lease_is_recovered_and_claims_are_exclusive(client, make_user, auth_header, qdrant_store):
    _user, headers, persona, _job_row, _draft = _import_owner(client, make_user, auth_header, "ix-lease@example.com")
    _review, version = _review_and_publish(client, headers, persona["id"], confirm=(0,))
    with db.SessionLocal() as first, db.SessionLocal() as second:
        claimed = knowledge_indexing._claim_one(first)
        assert claimed is not None and claimed.status == "indexing"
        assert knowledge_indexing._claim_one(second) is None
    with db.SessionLocal() as session:
        assert knowledge_indexing.recover_stale(session) == 0
        session.query(PersonaKnowledgeIndexJob).update({"claimed_at": datetime.utcnow() - timedelta(
            seconds=knowledge_indexing.LEASE_SECONDS + 5)})
        session.commit()
        assert knowledge_indexing.recover_stale(session) == 1
    recovered = _job(version["id"])
    assert recovered.status == "failed" and recovered.attempt_count == 1
    assert _index_all() == 1 and _job(version["id"]).status == "ready"


def test_version_restore_switches_retrieval_scope_and_stale_jobs_never_activate(
        client, make_user, auth_header, qdrant_store):
    user, headers, persona, _job_row, _draft = _import_owner(client, make_user, auth_header, "ix-restore@example.com")
    # Claim 0 (laundromat sales) is business-only and never retrieved for an owner
    # conversation, so the version markers are the general claims 2 (v1) and 1 (v2).
    review, v1 = _review_and_publish(client, headers, persona["id"], confirm=(2,))
    _review2, v2 = _review_and_publish(client, headers, persona["id"], confirm=(1, 2))
    statements = [claim["statement"] for claim in review["claims"]]

    # v2 is active but not yet indexed: no fallback to v1 or anyone else.
    context = _context(user.id)
    assert context.active_persona_version_id == v2["id"]
    assert knowledge.search_agent_knowledge(statements[1], context)["matches"] == []

    # Index v2 first, then a stale v1 job finishes after v2 is live.
    with db.SessionLocal() as session:
        session.query(PersonaKnowledgeIndexJob).filter_by(version_id=v1["id"]).update(
            {"next_attempt_at": datetime.utcnow() + timedelta(hours=1)})
        session.commit()
    assert _index_all() == 1
    with db.SessionLocal() as session:
        session.query(PersonaKnowledgeIndexJob).filter_by(version_id=v1["id"]).update(
            {"next_attempt_at": datetime.utcnow() - timedelta(seconds=1)})
        session.commit()
    assert _index_all() == 1
    with db.SessionLocal() as session:
        assert session.get(Persona, persona["id"]).active_version_id == v2["id"]

    live = knowledge.search_agent_knowledge(statements[1], _context(user.id), limit=6)["matches"]
    assert live and {item["published_version_id"] for item in live} == {v2["id"]}

    restored = client.post(f"{API}/{persona['id']}/versions/{v1['version_number']}/activate", headers=headers)
    assert restored.status_code == 200, restored.text
    old = knowledge.search_agent_knowledge(statements[2], _context(user.id), limit=6)["matches"]
    assert old and {item["published_version_id"] for item in old} == {v1["id"]}
    assert not any(statements[1] in item["text"] for item in old)


# ---------------------------------------------------------------------------
# Workstream 1: identity and isolation on real local engines
# ---------------------------------------------------------------------------

def _two_indexed_owners(client, make_user, auth_header, same_brokerage):
    a = _import_owner(client, make_user, auth_header, "iso-a@example.com", brokerage="brokerage-1", name="Agent A")
    b = _import_owner(client, make_user, auth_header, "iso-b@example.com",
                      brokerage="brokerage-1" if same_brokerage else "brokerage-2", name="Agent B")
    review, _va = _review_and_publish(client, a[1], a[2]["id"], confirm=(0, 1, 2))
    _review_and_publish(client, b[1], b[2]["id"], confirm=(0, 1, 2))
    assert _index_all() == 2
    return a[0], b[0], [claim["statement"] for claim in review["claims"]]


@pytest.mark.parametrize("same_brokerage", [True, False])
def test_qdrant_retrieval_is_isolated_per_owner_and_brokerage(client, make_user, auth_header, qdrant_store,
                                                              same_brokerage):
    a, b, statements = _two_indexed_owners(client, make_user, auth_header, same_brokerage)
    # Ownerless legacy vectors in the same collection are never returned.
    qdrant_store.add_texts(COLLECTION, ["bobbie-p1-c1"], [statements[0]],
                           metadatas=[{"page": 1, "heading": "Bobbie Fisher RE/MAX"}])
    for user in (a, b):
        context = _context(user.id)
        result = knowledge.search_agent_knowledge(
            f"{statements[0]} use iso-b@example.com, user_id {b.id if user is a else a.id}", context, 6)
        assert result["matches"]
        for match in result["matches"]:
            assert match["owner_user_id"] == user.id
            assert match["brokerage_id"] == context.brokerage_id
            assert match["published_version_id"] == context.active_persona_version_id
            assert not str(match["id"]).startswith("bobbie-")


def test_qdrant_server_side_filter_alone_enforces_scope(client, make_user, auth_header, qdrant_store):
    """Bypass the Python post-filter: the engine query itself returns only A's points."""
    a, _b, statements = _two_indexed_owners(client, make_user, auth_header, True)
    context = _context(a.id)
    hits = qdrant_store.search_text(COLLECTION, statements[0], k=20, filters={
        "owner_user_id": context.user_id, "brokerage_id": context.brokerage_id,
        "published_version_id": context.active_persona_version_id, "approval_status": "eligible"})
    assert hits and {hit["metadata"]["owner_user_id"] for hit in hits} == {a.id}


def test_chroma_retrieval_is_isolated_and_excludes_ownerless_vectors(client, make_user, auth_header, chroma_store):
    a, b, statements = _two_indexed_owners(client, make_user, auth_header, True)
    chroma_store.add_texts(COLLECTION, ["bobbie-p1-c1"], [statements[0]], metadatas=[{"page": 1}])
    context = _context(a.id)
    raw = chroma_store.search_text(COLLECTION, statements[0], k=20, filters={
        "owner_user_id": context.user_id, "brokerage_id": context.brokerage_id,
        "published_version_id": context.active_persona_version_id, "approval_status": "eligible"})
    assert raw and {hit["metadata"]["owner_user_id"] for hit in raw} == {a.id}
    matches = knowledge.search_agent_knowledge(statements[0], context, 6)["matches"]
    assert matches and all(match["owner_user_id"] == a.id for match in matches)


def test_unknown_or_unpublished_owner_gets_no_knowledge(client, make_user, auth_header, qdrant_store):
    _two_indexed_owners(client, make_user, auth_header, True)
    stranger = make_user("iso-none@example.com", role="agent")
    context = _context(stranger.id)
    assert context.active_persona_version_id is None
    assert knowledge.search_agent_knowledge("anything about the agent", context)["matches"] == []


def test_each_conversation_resolves_its_own_owner(client, make_user, auth_header):
    users = [
        _publish_manual(client, make_user, auth_header, f"who-{index}@example.com", brokerage, name)[0]
        for index, (brokerage, name) in enumerate([("b-remax", "Bobbie Fisher"), ("brokerage-1", "Michael Kang"),
                                                   ("brokerage-3", "Third Agent")])
    ]
    with db.SessionLocal() as session:
        for index, user in enumerate(users):
            conversation = _conversation(session, user.id, contact=f"+1312555990{index}")
            context = resolve_agent_runtime_context(session, conversation)
            assert context.user_id == user.id and context.brokerage_id == user.brokerage_id


# ---------------------------------------------------------------------------
# Workstream 2: dynamic prompts
# ---------------------------------------------------------------------------

@pytest.fixture
def three_agents(client, make_user, auth_header):
    bobbie_user = _publish_manual(client, make_user, auth_header, "bobbie@example.com", "b-remax", "Bobbie Fisher",
                                  brokerage_name="RE/MAX Premier",
                                  biography="Managing broker with RE/MAX Premier on the North Shore.")
    michael = _publish_manual(client, make_user, auth_header, "michael@example.com", "brokerage-1", "Michael Kang",
                              example={"scenario": "Korean outreach", "channel": "sms", "language": "ko",
                                       "customer_message": "사장님 매각 생각 있으신가요?",
                                       "preferred_response": KOREAN_OUTREACH, "provenance": "user_written"})
    third = _publish_manual(client, make_user, auth_header, "third@example.com", "brokerage-3", "Dana Rivera",
                            brokerage_name="Rivera Homes")
    return {"bobbie": bobbie_user, "michael": michael, "third": third}


def _prompt_for(user_id, latest=""):
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, contact=f"+1773555{user_id:04d}")
        context = resolve_agent_runtime_context(session, conversation)
        return context, build_agent_prompt(conversation, context, "", latest)


def test_each_agent_prompt_uses_only_their_own_published_persona(three_agents):
    bobbie_ctx, bobbie_prompt = _prompt_for(three_agents["bobbie"][0].id)
    assert "Bobbie Fisher" in bobbie_prompt and "RE/MAX Premier" in bobbie_prompt
    assert bobbie_ctx.active_persona_version_id == three_agents["bobbie"][3]["id"]
    for key, name, brokerage in (("michael", "Michael Kang", "Linchpin Global"),
                                 ("third", "Dana Rivera", "Rivera Homes")):
        context, prompt = _prompt_for(three_agents[key][0].id)
        assert f"Speak in first person as {name}" in prompt and brokerage in prompt
        assert context.active_persona_version_id == three_agents[key][3]["id"]
        for leak in ("Bobbie", "Fisher", "RE/MAX", "REMAX", "North Shore"):
            assert leak not in prompt, (key, leak)


def test_korean_examples_and_punctuation_survive_into_the_prompt(three_agents):
    _context_value, prompt = _prompt_for(three_agents["michael"][0].id, "사장님 매각 생각 있으신가요?")
    assert KOREAN_OUTREACH in prompt
    assert "\\u" not in prompt


def test_draft_edits_do_not_change_live_prompt_and_rollback_restores_it(client, three_agents):
    user, headers, persona, v1 = three_agents["third"]
    draft = client.get(f"{API}/{persona['id']}/draft", headers=headers).json()
    client.patch(f"{API}/{persona['id']}/draft", headers=headers,
                 json={"revision": draft["revision"], "biography": "UNPUBLISHED DRAFT BIO"})
    _ctx, prompt = _prompt_for(user.id)
    assert "UNPUBLISHED DRAFT BIO" not in prompt and "Dana Rivera biography" in prompt

    v2 = _republish(client, headers, persona["id"], biography="Version two biography")
    ctx2, prompt2 = _prompt_for(user.id)
    assert ctx2.active_persona_version_id == v2["id"] and "Version two biography" in prompt2

    restored = client.post(f"{API}/{persona['id']}/versions/{v1['version_number']}/activate", headers=headers)
    assert restored.status_code == 200, restored.text
    ctx1, prompt1 = _prompt_for(user.id)
    assert ctx1.active_persona_version_id == v1["id"]
    assert "Version two biography" not in prompt1 and "Dana Rivera biography" in prompt1


def test_no_published_persona_is_generic_never_bobbie(make_user):
    user = make_user("nopersona@example.com", role="agent", brokerage_name="Plain Realty")
    context, prompt = _prompt_for(user.id)
    assert context.active_persona_version_id is None and context.persona is None
    assert "Bobbie" not in prompt and "RE/MAX" not in prompt


def test_human_reply_suggestions_use_the_conversation_owners_persona(three_agents, monkeypatch):
    calls = scripted(monkeypatch, {"followups.*": [json.dumps({"suggestions": ["One.", "Two.", "Three."]})]})
    monkeypatch.setattr(ai_client, "is_configured", lambda: True)
    user = three_agents["third"][0]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id, history=[("outbound", "Hello"), ("inbound", "Who are you?")])
        reply_suggest.suggest(session, conversation, resolve_agent_runtime_context(session, conversation))
    system = calls[0]["messages"][0]["content"]
    assert "Dana Rivera" in system and "Rivera Homes" in system
    assert "Bobbie" not in system and "Michael Kang" not in system


def test_followup_uses_owner_identity_and_is_reviewed(three_agents, monkeypatch):
    calls = scripted(monkeypatch, {
        "sms.followup": ["Just checking in about 1 Test St—did you see my earlier note?"],
        "sms.review": [json.dumps({"valid": False, "issues": ["pressure"], "rewrite_instruction": "",
                                   "reason": "x"})],
    })
    user = three_agents["michael"][0]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id, history=[("outbound", "Hi, about 1 Test St?")])
        context = resolve_agent_runtime_context(session, conversation)
        message = bobbie.generate_no_response_followup(conversation, [], 1, 3, agent_context=context)
    assert "Michael Kang" in calls[0]["messages"][0]["content"]
    review = json.loads(calls[1]["messages"][1]["content"])
    assert review["trusted_agent"]["user_id"] == user.id
    # Rejected model text is replaced by the fixed, pre-written follow-up.
    assert message == "Just circling back—did you happen to see my earlier text about 1 Test St?"


def test_appointment_confirmation_names_the_owning_agent(three_agents):
    from app.sms import calendar_service
    text = calendar_service.confirmation_message(
        {"start_at": "2026-11-02T15:00:00", "end_at": "2026-11-02T15:30:00", "location_address": "1 Test St"},
        host=_context(three_agents["third"][0].id).agent_name)
    assert "Dana Rivera" in text and "Bobbie" not in text


# ---------------------------------------------------------------------------
# Reviewer: every candidate reviewed, bounded, fail closed
# ---------------------------------------------------------------------------

VALID = json.dumps({"valid": True, "issues": [], "rewrite_instruction": "", "reason": "ok"})
REJECT = json.dumps({"valid": False, "issues": ["unsupported_claim"], "rewrite_instruction": "Remove it.",
                     "reason": "unsupported"})
DISPOSITION = json.dumps({
    "action": "continue", "intent": "asks_question", "outcome": "neutral", "lead_status": "interested",
    "confidence": 0.9, "conversation_stage": "discovery", "next_step": "answer_only", "qualification_focus": "",
    "calendar": {"state": "none", "should_fetch_availability": False, "requested_time_text": "", "reason": ""},
    "reason": "question"})


def _reviews(calls):
    return [json.loads(call["messages"][1]["content"]) for call in calls if call["feature"] == "sms.review"]


def test_regenerated_candidates_reuse_one_evidence_snapshot(three_agents, monkeypatch):
    searches = []

    def fake_search(query, context, limit=4, **_options):
        searches.append(query)
        return {"query": query, "matches": [{"id": f"m{len(searches)}", "text": f"evidence {len(searches)}",
                                             "owner_user_id": context.user_id}]}

    monkeypatch.setattr(knowledge, "search_agent_knowledge", fake_search)
    calls = scripted(monkeypatch, {"sms.reply": ["Second draft.", "Third draft."],
                                   "sms.review": [REJECT, REJECT, VALID]})
    user = three_agents["michael"][0]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id, history=[("inbound", "What services do you offer?")])
        context = resolve_agent_runtime_context(session, conversation)
        history = [{"direction": "inbound", "text": "What services do you offer?"}]
        reply = bobbie.review_and_repair_ai_reply(
            conversation, history, "First draft.", {"next_step": "answer_only"},
            knowledge_evidence=[{"matches": [{"id": "m0"}]}], agent_context=context)
    assert reply == "Third draft."
    reviews = _reviews(calls)
    assert [item["proposed_agent_reply"] for item in reviews] == ["First draft.", "Second draft.", "Third draft."]
    assert reviews[0]["approved_knowledge_evidence"][0]["matches"][0]["id"] == "m0"
    # Knowledge is retrieved once for the attempt; every regeneration reuses it.
    assert searches == ["What services do you offer?"]
    assert reviews[1]["approved_knowledge_evidence"][0]["matches"][0]["id"] == "m1"
    assert reviews[2]["approved_knowledge_evidence"] == reviews[1]["approved_knowledge_evidence"]
    assert all(item["trusted_agent"]["user_id"] == user.id for item in reviews)
    assert all(item["trusted_agent"]["active_persona_version_id"] == context.active_persona_version_id
               for item in reviews)


def test_exhausted_or_unavailable_review_never_returns_a_draft(three_agents, monkeypatch):
    monkeypatch.setattr(knowledge, "search_agent_knowledge", lambda *a, **k: {"matches": []})
    user = three_agents["michael"][0]
    history = [{"direction": "inbound", "text": "Hello?"}]
    with db.SessionLocal() as session:
        conversation = _conversation(session, user.id)
        context = resolve_agent_runtime_context(session, conversation)
        scripted(monkeypatch, {"sms.reply": ["B.", "C."], "sms.review": [REJECT, REJECT, REJECT]})
        with pytest.raises(bobbie.ReviewRejectedError):
            bobbie.review_and_repair_ai_reply(conversation, history, "A.", {}, agent_context=context)
        scripted(monkeypatch, {"sms.review": [ai_client.AiUnavailableError("down")]})
        with pytest.raises(bobbie.ReviewUnavailableError):
            bobbie.review_and_repair_ai_reply(conversation, history, "A.", {}, agent_context=context)
        scripted(monkeypatch, {"sms.review": ["not json at all"]})
        with pytest.raises(bobbie.ReviewUnavailableError):
            bobbie.review_ai_reply(conversation, history, "A.", {}, agent_context=context)


def _pipeline(monkeypatch, user_id, script):
    sent = []
    monkeypatch.setattr(service, "send_and_store_message",
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or Message(id=1))
    monkeypatch.setattr(knowledge, "search_agent_knowledge", lambda *a, **k: {"matches": []})
    calls = scripted(monkeypatch, script)
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, history=[
            ("outbound", "Would you be open to a brief conversation?"), ("inbound", "What do you charge?")])
        conversation_id = conversation.id
    service.process_ai_reply(conversation_id, "What do you charge?")
    with db.SessionLocal() as session:
        conversation = session.get(Conversation, conversation_id)
        return sent, calls, conversation.handled_by, conversation.ai_enabled


def test_pipeline_sends_only_a_reviewed_regeneration(three_agents, monkeypatch):
    sent, calls, handled_by, _enabled = _pipeline(monkeypatch, three_agents["third"][0].id, {
        "sms.disposition": [DISPOSITION], "sms.reply": ["Draft one.", "Draft two."],
        "sms.review": [REJECT, VALID]})
    assert sent == [("Draft two.", "ai.reply")]
    assert [item["proposed_agent_reply"] for item in _reviews(calls)] == ["Draft one.", "Draft two."]
    assert handled_by == "bobbie"


def test_pipeline_sends_nothing_and_hands_off_when_every_candidate_is_rejected(three_agents, monkeypatch):
    sent, calls, handled_by, enabled = _pipeline(monkeypatch, three_agents["third"][0].id, {
        "sms.disposition": [DISPOSITION], "sms.reply": ["D1.", "D2.", "D3."],
        "sms.review": [REJECT, REJECT, REJECT]})
    assert sent == []
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS
    assert handled_by == "broker" and not enabled


def test_pipeline_sends_nothing_when_the_reviewer_is_down(three_agents, monkeypatch):
    sent, _calls, handled_by, enabled = _pipeline(monkeypatch, three_agents["third"][0].id, {
        "sms.disposition": [DISPOSITION], "sms.reply": ["Draft."],
        "sms.review": [ai_client.AiUnavailableError("provider down")]})
    assert sent == []
    assert handled_by == "bobbie" and enabled  # the next inbound message retries


CLOSING = json.dumps({
    "action": "end", "intent": "not_interested", "outcome": "negative", "lead_status": "not_interested",
    "confidence": 0.9, "conversation_stage": "complete", "next_step": "close", "qualification_focus": "",
    "calendar": {"state": "none", "should_fetch_availability": False, "requested_time_text": "", "reason": ""},
    "reason": "owner declined"})


def test_closing_path_reviews_every_candidate_and_falls_back_to_fixed_text(three_agents, monkeypatch):
    sent, calls, handled_by, enabled = _pipeline(monkeypatch, three_agents["third"][0].id, {
        "sms.disposition": [CLOSING], "sms.reply": ["Bye now, call me!", "Okay, ring me later?", "Talk soon?"],
        "sms.review": [REJECT, REJECT, REJECT]})
    # Every generated closing was reviewed and rejected; only the pre-written closing is sent, once.
    assert len(_reviews(calls)) == 1 + bobbie.MAX_REVIEW_REGENERATIONS
    assert sent == [("Understood. Thanks for letting me know, and take care.", "ai.closing")]
    assert handled_by == "broker" and not enabled


def test_delivery_error_is_not_retried_within_the_same_turn(three_agents, monkeypatch):
    attempts = []

    def failing_send(_session, _conversation, text, event, *rest):
        attempts.append(text)
        raise service.SmsDeliveryError("gateway down")

    monkeypatch.setattr(knowledge, "search_agent_knowledge", lambda *a, **k: {"matches": []})
    scripted(monkeypatch, {"sms.disposition": [DISPOSITION], "sms.reply": ["Draft."], "sms.review": [VALID]})
    monkeypatch.setattr(service, "send_and_store_message", failing_send)
    with db.SessionLocal() as session:
        conversation = _conversation(session, three_agents["third"][0].id, history=[("inbound", "Hi?")])
        conversation_id = conversation.id
    service.process_ai_reply(conversation_id, "Hi?")
    assert attempts == ["Draft."]  # exactly one send attempt, no duplicate


def test_persona_version_change_during_generation_sends_nothing(client, three_agents, monkeypatch):
    user, headers, persona, v1 = three_agents["third"]
    v2 = _republish(client, headers, persona["id"], biography="Version two biography")

    def reactivate_v1_then_accept():
        with db.SessionLocal() as session:
            session.get(Persona, persona["id"]).active_version_id = v1["id"]
            session.commit()
        return VALID

    sent, calls, _handled_by, _enabled = _pipeline(monkeypatch, user.id, {
        "sms.disposition": [DISPOSITION], "sms.reply": ["Draft."], "sms.review": [reactivate_v1_then_accept]})
    # Generated and reviewed under v2, but v1 became active before sending: nothing is sent.
    assert _reviews(calls)[0]["trusted_agent"]["active_persona_version_id"] == v2["id"]
    assert sent == []


def _due_followup(user_id):
    from app.sms import followup_scheduler
    with db.SessionLocal() as session:
        conversation = _conversation(session, user_id, history=[("outbound", "Hi, about 1 Test St?")])
        conversation.next_followup_at = datetime.utcnow() - timedelta(minutes=1)
        session.commit()
        return conversation.id, followup_scheduler


@pytest.mark.parametrize("verdict,expected", [
    (VALID, "Just checking in about 1 Test St—did you see my note?"),
    (REJECT, "Just circling back—did you happen to see my earlier text about 1 Test St?"),
    (ai_client.AiUnavailableError("review down"),
     "Just circling back—did you happen to see my earlier text about 1 Test St?"),
], ids=["accepted", "rejected", "reviewer_down"])
def test_scheduler_followup_is_reviewed_before_the_single_send(three_agents, monkeypatch, verdict, expected):
    user = three_agents["michael"][0]
    conversation_id, followup_scheduler = _due_followup(user.id)
    monkeypatch.setitem(settings["sms"], "followup_max_attempts", 3)
    calls = scripted(monkeypatch, {"sms.followup": ["Just checking in about 1 Test St—did you see my note?"],
                                   "sms.review": [verdict]})
    sent = []
    monkeypatch.setattr(followup_scheduler, "send_and_store_message",
                        lambda _s, _c, text, event, *rest: sent.append((text, event)) or Message(id=1))
    with db.SessionLocal() as session:
        assert followup_scheduler.process_due_conversation(session, conversation_id) == "sent"
    assert sent == [(expected, "ai.followup.1")]
    review = json.loads(calls[1]["messages"][1]["content"])
    assert review["trusted_agent"]["user_id"] == user.id
    assert review["proposed_agent_reply"] == "Just checking in about 1 Test St—did you see my note?"


def test_ai_debug_explainer_never_crashes_on_identity_switch(monkeypatch):
    """Debug output runs inside generation; it must not raise for any violation."""
    from app.sms.policy import explain_policy_violations, find_reply_policy_violations
    text = "Dana Rivera's assistant here. Dana would love to stop by."
    violations = find_reply_policy_violations(text, phone_source_known=False, agent_name="Dana Rivera")
    assert "identity_switch" in violations
    reasons = explain_policy_violations(text, violations, [])
    assert "identity_switch" in reasons
