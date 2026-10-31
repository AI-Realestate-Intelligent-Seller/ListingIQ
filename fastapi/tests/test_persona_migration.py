"""Persona migrations and database-level retention on disposable databases.

SQLite always runs against a throwaway file. PostgreSQL runs only when
``PERSONA_TEST_PG_URL`` points at a disposable database (for example a local
``initdb`` cluster); the test drops and recreates that database's ``public``
schema, so never point it at a shared or production database.
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import sessionmaker

BASE_DIR = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("PERSONA_TEST_PG_URL", "")
PREVIOUS = "0031_campaign_draft_groups"
HEAD = "0037_persona_storage_indexing"
BEFORE_FLAGS = "0034_persona_import_jobs"
BEFORE_STORAGE = "0036_persona_import_mode"


def _alembic(url: str, *args: str) -> str:
    # A child process so env.py reads this disposable URL, not the test app's.
    env = {**os.environ, "POSTGRES_URL": url}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def _seed(conn):
    conn.execute(
        text(
            "INSERT INTO users (id, email, hashed_password, brokerage_id, role, is_active)"
            " VALUES (1, 'owner@example.com', 'x', 'b1', 'broker', true)"
        )
    )
    conn.execute(
        text(
            "INSERT INTO personas (id, brokerage_id, owner_user_id, slug, display_name,"
            " scope, status, created_by_user_id, created_at, updated_at)"
            " VALUES (1, 'b1', 1, 'michael-ai', 'Michael AI', 'personal', 'active', 1,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(
        text(
            "INSERT INTO persona_versions (id, persona_id, version_number, full_name,"
            " display_name, professional_role, brokerage_name, biography,"
            " experience_summary, specialties, service_areas, sms_signature, style_config,"
            " style_instructions, preferred_language, content_hash, style_schema_version,"
            " prompt_schema_version, published_by_user_id, published_at)"
            " VALUES (1, 1, 1, 'Michael Kang', 'Michael AI', 'Broker', 'Linchpin', '', '',"
            " '[]', '[]', 'Michael', '{}', '', 'en', 'hash-1', 1, 1, 1, CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(
        text(
            "INSERT INTO persona_submissions (id, persona_id, submission_number,"
            " draft_revision, snapshot, content_hash, status, submitted_by_user_id,"
            " submitted_at, published_version_id)"
            " VALUES (1, 1, 1, 2, :snapshot, 'hash-1', 'published', 1, CURRENT_TIMESTAMP, 1)"
        ),
        {"snapshot": json.dumps({"biography": "approved"})},
    )
    conn.execute(
        text(
            "INSERT INTO persona_submissions (id, persona_id, submission_number,"
            " draft_revision, snapshot, content_hash, status, submitted_by_user_id,"
            " submitted_at)"
            " VALUES (2, 1, 2, 3, :snapshot, 'hash-2', 'submitted', 1, CURRENT_TIMESTAMP)"
        ),
        {"snapshot": json.dumps({"biography": "pending"})},
    )


REJECTED_STATEMENTS = [
    "UPDATE persona_versions SET display_name = 'Mutated' WHERE id = 1",
    "UPDATE persona_versions SET persona_id = 1 WHERE id = 1",
    "DELETE FROM persona_versions WHERE id = 1",
    "DELETE FROM personas WHERE id = 1",
    "UPDATE persona_submissions SET snapshot = '{\"biography\": \"forged\"}' WHERE id = 2",
    "UPDATE persona_submissions SET content_hash = 'forged' WHERE id = 2",
    "UPDATE persona_submissions SET draft_revision = 9 WHERE id = 2",
    "UPDATE persona_submissions SET status = 'rejected' WHERE id = 1",
    "UPDATE persona_submissions SET published_version_id = NULL WHERE id = 1",
    "DELETE FROM persona_submissions WHERE id = 2",
    # A second pending submission for the same Persona.
    "INSERT INTO persona_submissions (id, persona_id, submission_number, draft_revision,"
    " snapshot, content_hash, status, submitted_by_user_id, submitted_at)"
    " VALUES (3, 1, 3, 4, '{}', 'hash-3', 'submitted', 1, CURRENT_TIMESTAMP)",
    "UPDATE personas SET scope = 'brokerage' WHERE id = 1",
]


def _assert_retention_rules(engine):
    with engine.begin() as conn:
        _seed(conn)
    for statement in REJECTED_STATEMENTS:
        with pytest.raises((IntegrityError, DBAPIError)):
            with engine.begin() as conn:
                conn.execute(text(statement))
    with engine.begin() as conn:
        # The pending submission may still be decided once; review state is
        # the only mutable part of a submission.
        conn.execute(
            text(
                "UPDATE persona_submissions SET status = 'rejected',"
                " rejection_reason = 'Needs licence number' WHERE id = 2"
            )
        )
        # Rollback-style activation only moves the pointer on personas.
        conn.execute(text("UPDATE personas SET active_version_id = 1 WHERE id = 1"))
        conn.execute(text("UPDATE personas SET status = 'archived' WHERE id = 1"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT display_name FROM persona_versions")).scalar() == (
            "Michael AI"
        )
        assert conn.execute(text("SELECT count(*) FROM persona_versions")).scalar() == 1
        assert conn.execute(text("SELECT count(*) FROM persona_submissions")).scalar() == 2


def _assert_membership_trigger(engine):
    """Raw-SQL brokerage changes end the membership; nothing reattaches it."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, email, hashed_password, brokerage_id, role, is_active)"
                " VALUES (2, 'mover@example.com', 'x', 'bA', 'agent', true)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO personas (id, brokerage_id, owner_user_id, slug, display_name,"
                " scope, status, created_by_user_id, created_at, updated_at)"
                " VALUES (2, 'bA', 2, 'mover-ai', 'Mover AI', 'personal', 'active', 2,"
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(text("UPDATE users SET first_name = 'Renamed' WHERE id = 2"))
        conn.execute(text("UPDATE users SET brokerage_id = 'bA' WHERE id = 2"))
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT membership_ended_at FROM personas WHERE id = 2")).scalar()
            is None
        )
    with engine.begin() as conn:
        conn.execute(text("UPDATE users SET brokerage_id = 'bB' WHERE id = 2"))
        conn.execute(text("UPDATE users SET brokerage_id = 'bA' WHERE id = 2"))
    with engine.connect() as conn:
        assert (
            conn.execute(text("SELECT membership_ended_at FROM personas WHERE id = 2")).scalar()
            is not None
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM persona_events WHERE persona_id = 2"
                    " AND event_type = 'persona.owner_membership_ended'"
                )
            ).scalar()
            == 1
        )


def _persona_tables(engine):
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            rows = conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'persona%'")
            )
        else:
            rows = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    " AND tablename LIKE 'persona%'"
                )
            )
        return {row[0] for row in rows}


ALL_TABLES = {
    "personas",
    "persona_versions",
    "persona_submissions",
    "persona_drafts",
    "persona_defaults",
    "persona_events",
    "persona_draft_examples",
    "persona_submission_examples",
    "persona_version_examples",
    "persona_import_jobs",
    "persona_knowledge_index_jobs",
}


def _membership_trigger_present(engine):
    with engine.connect() as conn:
        if engine.dialect.name == "sqlite":
            query = "SELECT count(*) FROM sqlite_master WHERE type='trigger' AND name = :n"
        else:
            query = "SELECT count(*) FROM pg_trigger WHERE tgname = :n"
        return bool(conn.execute(text(query), {"n": "trg_users_persona_membership_ended"}).scalar())


def _round_trip(url, engine):
    _alembic(url, "upgrade", HEAD)
    assert _persona_tables(engine) == ALL_TABLES
    assert _membership_trigger_present(engine)
    _alembic(url, "downgrade", PREVIOUS)
    assert _persona_tables(engine) == set()
    assert not _membership_trigger_present(engine)
    _alembic(url, "upgrade", HEAD)
    assert _persona_tables(engine) == ALL_TABLES
    assert HEAD in _alembic(url, "current")


def test_sqlite_upgrade_downgrade_and_retention(tmp_path):
    url = f"sqlite:///{tmp_path / 'persona_migration.db'}"
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys = ON")

    try:
        _round_trip(url, engine)
        _assert_retention_rules(engine)
        _assert_membership_trigger(engine)
    finally:
        engine.dispose()


_EXAMPLE_INSERT = (
    "INSERT INTO {table} (id, {parent}, position, scenario, channel, language, customer_message,"
    " preferred_response, context, style_explanation, provenance, owner_approved)"
    " VALUES (1, 1, 0, 'Valuation', 'sms', 'ko', '', '안녕하세요 사장님', '', '', 'illustrative', 0)"
)
_FROZEN_EXAMPLES = (("persona_submission_examples", "submission_id"), ("persona_version_examples", "version_id"))


def _assert_frozen_examples_immutable(engine):
    for table, _parent in _FROZEN_EXAMPLES:
        for statement in (f"UPDATE {table} SET preferred_response = 'forged' WHERE id = 1", f"DELETE FROM {table} WHERE id = 1"):
            with pytest.raises((IntegrityError, DBAPIError)):
                with engine.begin() as conn:
                    conn.execute(text(statement))


def _review_flags_round_trip(url, engine):
    """0035 keeps frozen examples readable and immutable in both directions."""
    _alembic(url, "upgrade", BEFORE_FLAGS)
    with engine.begin() as conn:
        _seed(conn)
        for table, parent in _FROZEN_EXAMPLES:
            conn.execute(text(_EXAMPLE_INSERT.format(table=table, parent=parent)))
    _alembic(url, "upgrade", HEAD)
    with engine.connect() as conn:
        for table, _parent in _FROZEN_EXAMPLES:
            row = conn.execute(text(f"SELECT review_flags, preferred_response FROM {table} WHERE id = 1")).one()
            flags = row[0] if isinstance(row[0], list) else json.loads(row[0])
            assert flags == [] and row[1] == "안녕하세요 사장님"
    _assert_frozen_examples_immutable(engine)
    _alembic(url, "downgrade", BEFORE_FLAGS)
    _assert_frozen_examples_immutable(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT preferred_response FROM persona_version_examples")).scalar() == "안녕하세요 사장님"
    _alembic(url, "upgrade", HEAD)
    _assert_frozen_examples_immutable(engine)


def test_sqlite_review_flags_migration_keeps_examples_immutable(tmp_path):
    url = f"sqlite:///{tmp_path / 'persona_flags.db'}"
    engine = create_engine(url)
    try:
        _review_flags_round_trip(url, engine)
    finally:
        engine.dispose()


def _storage_round_trip(url, engine):
    """0037 keeps database-stored originals byte-for-byte and marks them 'database'."""
    original = "안녕하세요 — original bytes".encode("utf-8") + bytes(range(256))
    _alembic(url, "upgrade", BEFORE_STORAGE)
    with engine.begin() as conn:
        _seed(conn)
        conn.execute(
            text(
                "INSERT INTO persona_import_jobs (id, persona_id, brokerage_id, owner_user_id, base_revision,"
                " file_name, media_type, file_size, file_hash, document_bytes, status, stages, retryable,"
                " attempt_count, extraction_mode, created_at, updated_at)"
                " VALUES ('legacyjob', 1, 'b1', 1, 1, 'bio.txt', 'text/plain', :size, 'h', :content,"
                " 'completed', '{}', 0, 1, 'local', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"size": len(original), "content": original},
        )
    _alembic(url, "upgrade", HEAD)
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT document_bytes, storage_backend, storage_key FROM persona_import_jobs WHERE id = 'legacyjob'"
        )).one()
        assert bytes(row[0]) == original and row[1] == "database" and row[2] is None
    with engine.begin() as conn:
        # Object-storage rows need no database copy after 0037.
        conn.execute(text(
            "INSERT INTO persona_import_jobs (id, persona_id, brokerage_id, owner_user_id, base_revision,"
            " file_name, media_type, file_size, file_hash, document_bytes, storage_backend, storage_key, status,"
            " stages, retryable, attempt_count, extraction_mode, created_at, updated_at)"
            " VALUES ('r2job', 1, 'b1', 1, 1, 'bio.pdf', 'application/pdf', 3, 'h2', NULL, 'r2',"
            " 'personas/brokerage-b1/user-1/imports/r2job/original.pdf', 'queued', '{}', 0, 0, 'local',"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        conn.execute(text(
            "INSERT INTO persona_knowledge_index_jobs (id, persona_id, version_id, owner_user_id, brokerage_id,"
            " source_document_id, status, content_hash, chunk_count, attempt_count, max_attempts,"
            " next_attempt_at, created_at, updated_at) VALUES ('ix1', 1, 1, 1, 'b1', 'legacyjob', 'pending',"
            " 'c', 1, 0, 3, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
    with pytest.raises((IntegrityError, DBAPIError)):
        with engine.begin() as conn:
            conn.execute(text("UPDATE persona_knowledge_index_jobs SET status = 'bogus' WHERE id = 'ix1'"))
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM persona_knowledge_index_jobs"))
        conn.execute(text("DELETE FROM persona_import_jobs WHERE id = 'r2job'"))
    _alembic(url, "downgrade", BEFORE_STORAGE)
    with engine.connect() as conn:
        assert bytes(conn.execute(text(
            "SELECT document_bytes FROM persona_import_jobs WHERE id = 'legacyjob'")).scalar()) == original
    _alembic(url, "upgrade", HEAD)


def test_sqlite_storage_migration_preserves_database_originals(tmp_path):
    url = f"sqlite:///{tmp_path / 'persona_storage.db'}"
    engine = create_engine(url)
    try:
        _storage_round_trip(url, engine)
    finally:
        engine.dispose()


def test_single_alembic_head():
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        cwd=BASE_DIR,
        env={**os.environ, "POSTGRES_URL": "sqlite://"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    heads = [line for line in result.stdout.splitlines() if line.strip()]
    assert heads == [f"{HEAD} (head)"], result.stdout + result.stderr


# --------------------------------------------------------------------------
# PostgreSQL (disposable database only)
# --------------------------------------------------------------------------

pg = pytest.mark.skipif(not PG_URL, reason="PERSONA_TEST_PG_URL is not set")


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(PG_URL, future=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


@pg
def test_postgres_upgrade_downgrade_and_retention(pg_engine):
    _round_trip(PG_URL, pg_engine)
    _assert_retention_rules(pg_engine)
    _assert_membership_trigger(pg_engine)


@pg
def test_postgres_review_flags_migration_keeps_examples_immutable(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    _review_flags_round_trip(PG_URL, pg_engine)


@pg
def test_postgres_concurrent_owner_edits_and_submissions(pg_engine):
    """Real PostgreSQL sessions racing on the same draft revision."""
    from app.models import User
    from app.personas import service
    from app.personas.models import Persona, PersonaEvent, PersonaSubmission
    from app.personas.schemas import PersonaCreate, PersonaDraftUpdate, PersonaExampleCreate
    from test_persona_onboarding import complete_profile

    # Start from a freshly migrated schema (the retention test seeds explicit IDs).
    with pg_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    _alembic(PG_URL, "upgrade", HEAD)
    Session = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    setup = Session()
    owner = User(
        email="pg-owner@example.com",
        hashed_password="x",
        brokerage_id="pg-brokerage",
        brokerage_name="PG Realty",
        role="broker",
        is_active=True,
    )
    setup.add(owner)
    setup.commit()
    owner_id = owner.id
    persona = service.create(
        setup, setup.get(User, owner_id), PersonaCreate(full_name="Pat Postgres")
    )
    persona_id = persona.id
    # Milestone 2 completeness: a full profile and one owner-approved example,
    # so the submit race below exercises concurrency, not validation.
    service.update_draft(setup, setup.get(User, owner_id), persona_id, PersonaDraftUpdate(
        revision=1, professional_title="Broker", biography="Ready.", experience_summary="Experienced.",
        specialties=["Residential"], service_areas=["Toronto"], profile_config=complete_profile(),
    ))
    service.create_example(setup, setup.get(User, owner_id), persona_id, PersonaExampleCreate(
        revision=2, scenario="Price objection", channel="sms", customer_message="That feels low.",
        preferred_response="Can I show you the comparable sales?", provenance="user_written", owner_approved=True,
    ))
    assert service.validation_result(setup.get(Persona, persona_id).draft,
                                     setup.get(Persona, persona_id).draft_examples)["valid"]
    setup.close()

    def race(workers, action):
        barrier = threading.Barrier(workers)
        outcomes = []
        lock = threading.Lock()

        def run(index):
            session = Session()
            try:
                actor = session.get(User, owner_id)
                held = session.get(Persona, persona_id)
                assert held.draft is not None
                barrier.wait()
                action(session, actor, index)
                outcome = "ok"
            except service.PersonaConflict:
                session.rollback()
                outcome = "conflict"
            except Exception as error:  # pragma: no cover - reported below
                session.rollback()
                outcome = f"error: {error!r}"
            finally:
                session.close()
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        return outcomes

    edits = race(
        8,
        lambda session, actor, i: service.update_draft(
            session,
            actor,
            persona_id,
            PersonaDraftUpdate(
                revision=3,
                biography=f"writer {i}",
                experience_summary="Experienced.",
            ),
        ),
    )
    assert sorted(edits) == ["conflict"] * 7 + ["ok"], edits

    submits = race(
        6, lambda session, actor, i: service.submit(session, actor, persona_id, 4)
    )
    assert sorted(submits) == ["conflict"] * 5 + ["ok"], submits

    check = Session()
    try:
        draft = check.get(Persona, persona_id).draft
        assert draft.revision == 4
        assert draft.review_status == "submitted"
        # One from setup, exactly one from the eight racing edits.
        assert (
            check.query(PersonaEvent)
            .filter_by(persona_id=persona_id, event_type="persona.draft_changed")
            .count()
            == 2
        )
        assert (
            check.query(PersonaEvent)
            .filter_by(persona_id=persona_id, event_type="persona.draft_submitted")
            .count()
            == 1
        )
        assert check.query(PersonaSubmission).filter_by(persona_id=persona_id).count() == 1
    finally:
        check.close()


@pg
def test_postgres_storage_migration_preserves_database_originals(pg_engine):
    with pg_engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    _storage_round_trip(PG_URL, pg_engine)
