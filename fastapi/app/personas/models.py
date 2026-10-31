"""SQLAlchemy models for immutable Persona identity and style versions."""

from datetime import datetime

from sqlalchemy import (
    DDL,
    JSON,
    LargeBinary,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import relationship

from ..db import Base


class Persona(Base):
    __tablename__ = "personas"
    __table_args__ = (
        UniqueConstraint("brokerage_id", "slug", name="uq_personas_brokerage_slug"),
        # Every Persona is one user's own speaking identity. A brokerage-wide
        # speaking Persona is intentionally not supported.
        CheckConstraint("scope = 'personal'", name="ck_personas_scope"),
        CheckConstraint("status IN ('active', 'archived')", name="ck_personas_status"),
        Index(
            "ix_personas_brokerage_owner_status",
            "brokerage_id",
            "owner_user_id",
            "status",
        ),
    )

    id = Column(Integer, primary_key=True)
    brokerage_id = Column(String(255), nullable=False, index=True)
    owner_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    slug = Column(String(100), nullable=False)
    display_name = Column(String(120), nullable=False)
    scope = Column(String(20), nullable=False, default="personal")
    status = Column(String(20), nullable=False, default="active", index=True)
    active_version_id = Column(
        Integer,
        ForeignKey(
            "persona_versions.id",
            name="fk_personas_active_version_id",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        index=True,
    )
    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Set by the database when the owner's brokerage membership ends (any
    # change of users.brokerage_id). Never cleared: rejoining the brokerage
    # is a fresh membership and needs a fresh Persona.
    membership_ended_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    draft = relationship(
        "PersonaDraft",
        back_populates="persona",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # Published versions and review submissions are retained history: the ORM
    # never cascades a delete to them or nulls their foreign key, so the
    # database RESTRICT/immutability rules always decide.
    versions = relationship(
        "PersonaVersion",
        back_populates="persona",
        foreign_keys="PersonaVersion.persona_id",
        order_by="PersonaVersion.version_number",
        cascade="save-update, merge",
        passive_deletes="all",
    )
    draft_examples = relationship(
        "PersonaDraftExample",
        back_populates="persona",
        cascade="all, delete-orphan",
        order_by="PersonaDraftExample.position",
    )
    import_jobs = relationship(
        "PersonaImportJob",
        back_populates="persona",
        cascade="all, delete-orphan",
        order_by="PersonaImportJob.created_at.desc()",
    )
    submissions = relationship(
        "PersonaSubmission",
        back_populates="persona",
        order_by="PersonaSubmission.submission_number",
        cascade="save-update, merge",
        passive_deletes="all",
    )
    active_version = relationship(
        "PersonaVersion", foreign_keys=[active_version_id], post_update=True
    )


@event.listens_for(Persona, "before_delete", propagate=True)
def _prevent_persona_with_history_delete(_mapper, connection, target) -> None:
    # Personas are archived, not deleted, once they have review or publication
    # history. The database enforces the same rule through RESTRICT keys.
    for table in ("persona_versions", "persona_submissions"):
        if connection.execute(
            text(f"SELECT 1 FROM {table} WHERE persona_id = :id LIMIT 1"),
            {"id": target.id},
        ).first():
            raise ValueError(
                "Personas with review or publication history must be archived"
            )


class PersonaVersion(Base):
    __tablename__ = "persona_versions"
    __table_args__ = (
        UniqueConstraint(
            "persona_id", "version_number", name="uq_persona_versions_number"
        ),
        UniqueConstraint(
            "persona_id", "content_hash", name="uq_persona_versions_content_hash"
        ),
        CheckConstraint(
            "version_number > 0", name="ck_persona_versions_positive_number"
        ),
        Index("ix_persona_versions_persona_published", "persona_id", "published_at"),
    )

    id = Column(Integer, primary_key=True)
    persona_id = Column(
        Integer,
        ForeignKey("personas.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    full_name = Column(String(255), nullable=False)
    display_name = Column(String(120), nullable=False)
    professional_role = Column(String(120), nullable=False)
    professional_title = Column(String(160), nullable=True)
    brokerage_name = Column(String(255), nullable=False)
    biography = Column(Text, nullable=False, default="")
    experience_summary = Column(Text, nullable=False, default="")
    specialties = Column(JSON, nullable=False, default=list)
    service_areas = Column(JSON, nullable=False, default=list)
    sms_signature = Column(String(320), nullable=False)
    style_config = Column(JSON, nullable=False, default=dict)
    style_instructions = Column(Text, nullable=False, default="")
    preferred_language = Column(String(40), nullable=False, default="en")
    profile_config = Column(JSON, nullable=False, default=dict)
    content_hash = Column(String(64), nullable=False)
    style_schema_version = Column(Integer, nullable=False, default=1)
    prompt_schema_version = Column(Integer, nullable=False, default=1)
    published_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    published_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    persona = relationship(
        "Persona", back_populates="versions", foreign_keys=[persona_id]
    )
    examples = relationship(
        "PersonaVersionExample",
        back_populates="version",
        cascade="save-update, merge",
        passive_deletes="all",
        order_by="PersonaVersionExample.position",
    )


@event.listens_for(PersonaVersion, "before_update", propagate=True)
def _prevent_published_version_update(_mapper, _connection, _target) -> None:
    raise ValueError("Published Persona versions are immutable")


@event.listens_for(PersonaVersion, "before_delete", propagate=True)
def _prevent_published_version_delete(_mapper, _connection, _target) -> None:
    raise ValueError("Published Persona versions must be retained")


class PersonaSubmission(Base):
    """Immutable review material plus mutable approval state."""

    __tablename__ = "persona_submissions"
    __table_args__ = (
        UniqueConstraint(
            "persona_id",
            "submission_number",
            name="uq_persona_submissions_number",
        ),
        UniqueConstraint(
            "persona_id",
            "draft_revision",
            name="uq_persona_submissions_draft_revision",
        ),
        CheckConstraint(
            "submission_number > 0",
            name="ck_persona_submissions_positive_number",
        ),
        CheckConstraint(
            "draft_revision > 0",
            name="ck_persona_submissions_positive_revision",
        ),
        CheckConstraint(
            "status IN ('submitted', 'published', 'rejected')",
            name="ck_persona_submissions_status",
        ),
        Index(
            "ix_persona_submissions_persona_status",
            "persona_id",
            "status",
        ),
        # At most one submission per Persona may await review at a time.
        Index(
            "uq_persona_submissions_one_pending",
            "persona_id",
            unique=True,
            sqlite_where=text("status = 'submitted'"),
            postgresql_where=text("status = 'submitted'"),
        ),
    )

    id = Column(Integer, primary_key=True)
    persona_id = Column(
        Integer,
        ForeignKey("personas.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    submission_number = Column(Integer, nullable=False)
    draft_revision = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="submitted", index=True)
    submitted_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    reviewed_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reviewed_at = Column(DateTime, nullable=True)
    rejection_reason = Column(String(500), nullable=True)
    published_version_id = Column(
        Integer,
        ForeignKey("persona_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    persona = relationship("Persona", back_populates="submissions")
    published_version = relationship(
        "PersonaVersion", foreign_keys=[published_version_id]
    )
    examples = relationship(
        "PersonaSubmissionExample",
        back_populates="submission",
        cascade="save-update, merge",
        passive_deletes="all",
        order_by="PersonaSubmissionExample.position",
    )


SUBMISSION_IMMUTABLE_FIELDS = (
    "persona_id",
    "submission_number",
    "draft_revision",
    "snapshot",
    "content_hash",
    "submitted_by_user_id",
    "submitted_at",
)


@event.listens_for(PersonaSubmission, "before_update", propagate=True)
def _prevent_submission_snapshot_update(_mapper, _connection, target) -> None:
    state = inspect(target)
    if any(
        getattr(state.attrs, field).history.has_changes()
        for field in SUBMISSION_IMMUTABLE_FIELDS
    ):
        raise ValueError("Submitted Persona review snapshots are immutable")
    status = state.attrs.status.history
    if status.has_changes() and (status.deleted or [None])[0] != "submitted":
        raise ValueError("A reviewed Persona submission cannot change state again")


class PersonaDraft(Base):
    __tablename__ = "persona_drafts"
    __table_args__ = (
        CheckConstraint("revision > 0", name="ck_persona_drafts_positive_revision"),
        CheckConstraint(
            "review_status IN ('draft', 'submitted', 'published')",
            name="ck_persona_drafts_review_status",
        ),
    )

    persona_id = Column(
        Integer, ForeignKey("personas.id", ondelete="CASCADE"), primary_key=True
    )
    base_version_id = Column(
        Integer,
        ForeignKey("persona_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    revision = Column(Integer, nullable=False, default=1)
    full_name = Column(String(255), nullable=False)
    display_name = Column(String(120), nullable=False)
    professional_role = Column(String(120), nullable=False, default="")
    professional_title = Column(String(160), nullable=True)
    brokerage_name = Column(String(255), nullable=False, default="")
    biography = Column(Text, nullable=False, default="")
    experience_summary = Column(Text, nullable=False, default="")
    specialties = Column(JSON, nullable=False, default=list)
    service_areas = Column(JSON, nullable=False, default=list)
    sms_signature = Column(String(320), nullable=False, default="")
    style_config = Column(JSON, nullable=False, default=dict)
    style_instructions = Column(Text, nullable=False, default="")
    preferred_language = Column(String(40), nullable=False, default="en")
    profile_config = Column(JSON, nullable=False, default=dict)
    review_status = Column(String(20), nullable=False, default="draft", index=True)
    submitted_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    submitted_at = Column(DateTime, nullable=True)
    last_editor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    persona = relationship("Persona", back_populates="draft", foreign_keys=[persona_id])
    base_version = relationship("PersonaVersion", foreign_keys=[base_version_id])


class PersonaDraftExample(Base):
    """Owner-editable examples; every mutation advances the parent draft revision."""

    __tablename__ = "persona_draft_examples"
    __table_args__ = (
        CheckConstraint("position >= 0", name="ck_persona_draft_examples_position"),
        CheckConstraint("channel IN ('sms', 'email')", name="ck_persona_draft_examples_channel"),
        CheckConstraint(
            "provenance IN ('authentic', 'user_written', 'illustrative')",
            name="ck_persona_draft_examples_provenance",
        ),
        Index("ix_persona_draft_examples_persona_position", "persona_id", "position"),
        Index("ix_persona_draft_examples_persona_channel", "persona_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    persona_id = Column(Integer, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False, default=0)
    scenario = Column(String(120), nullable=False)
    channel = Column(String(20), nullable=False)
    language = Column(String(40), nullable=False, default="en")
    customer_message = Column(Text, nullable=False)
    preferred_response = Column(Text, nullable=False)
    context = Column(Text, nullable=False, default="")
    style_explanation = Column(Text, nullable=False, default="")
    provenance = Column(String(30), nullable=False)
    # Import review warnings (consent, provenance). Server-set; never cleared by edits.
    review_flags = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    owner_approved = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    persona = relationship("Persona", back_populates="draft_examples")


class PersonaSubmissionExample(Base):
    """Frozen example copied from a specific draft revision for HOB review."""

    __tablename__ = "persona_submission_examples"
    __table_args__ = (
        UniqueConstraint("submission_id", "position", name="uq_persona_submission_examples_position"),
        Index("ix_persona_submission_examples_submission", "submission_id", "position"),
    )

    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey("persona_submissions.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_example_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False)
    scenario = Column(String(120), nullable=False)
    channel = Column(String(20), nullable=False)
    language = Column(String(40), nullable=False)
    customer_message = Column(Text, nullable=False)
    preferred_response = Column(Text, nullable=False)
    context = Column(Text, nullable=False, default="")
    style_explanation = Column(Text, nullable=False, default="")
    provenance = Column(String(30), nullable=False)
    # Import review warnings (consent, provenance). Server-set; never cleared by edits.
    review_flags = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    owner_approved = Column(Integer, nullable=False)

    submission = relationship("PersonaSubmission", back_populates="examples")


class PersonaVersionExample(Base):
    """Immutable example belonging to exactly one published Persona version."""

    __tablename__ = "persona_version_examples"
    __table_args__ = (
        UniqueConstraint("version_id", "position", name="uq_persona_version_examples_position"),
        Index("ix_persona_version_examples_version", "version_id", "position"),
    )

    id = Column(Integer, primary_key=True)
    version_id = Column(Integer, ForeignKey("persona_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    source_submission_example_id = Column(Integer, nullable=True)
    position = Column(Integer, nullable=False)
    scenario = Column(String(120), nullable=False)
    channel = Column(String(20), nullable=False)
    language = Column(String(40), nullable=False)
    customer_message = Column(Text, nullable=False)
    preferred_response = Column(Text, nullable=False)
    context = Column(Text, nullable=False, default="")
    style_explanation = Column(Text, nullable=False, default="")
    provenance = Column(String(30), nullable=False)
    # Import review warnings (consent, provenance). Server-set; never cleared by edits.
    review_flags = Column(JSON, nullable=False, default=list, server_default=text("'[]'"))
    owner_approved = Column(Integer, nullable=False)

    version = relationship("PersonaVersion", back_populates="examples")


class PersonaImportJob(Base):
    """Durable, owner-scoped processing state for one Persona source document."""

    __tablename__ = "persona_import_jobs"
    __table_args__ = (
        UniqueConstraint("persona_id", "file_hash", name="uq_persona_import_jobs_file_hash"),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'applied')",
            name="ck_persona_import_jobs_status",
        ),
        Index("ix_persona_import_jobs_owner_created", "owner_user_id", "created_at"),
        Index("ix_persona_import_jobs_status_updated", "status", "updated_at"),
    )

    id = Column(String(32), primary_key=True)
    persona_id = Column(Integer, ForeignKey("personas.id", ondelete="CASCADE"), nullable=False, index=True)
    brokerage_id = Column(String(255), nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    base_revision = Column(Integer, nullable=False)
    file_name = Column(String(255), nullable=False)
    media_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=False)
    # Nullable since 0037: an object-storage upload need not duplicate the file here.
    document_bytes = Column(LargeBinary, nullable=True)
    storage_backend = Column(String(20), nullable=False, default="database", server_default=text("'database'"))
    storage_key = Column(String(900), nullable=True)
    document_text = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="queued", index=True)
    current_stage = Column(String(50), nullable=True)
    stages = Column(JSON, nullable=False, default=dict)
    extraction = Column(JSON, nullable=True)
    proposal = Column(JSON, nullable=True)
    assessment = Column(JSON, nullable=True)
    error_code = Column(String(80), nullable=True)
    error_message = Column(String(500), nullable=True)
    retryable = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    # "local" reads the ListingIQ template without AI; "ai" only after the owner asks.
    extraction_mode = Column(String(20), nullable=False, default="local", server_default=text("'local'"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    persona = relationship("Persona", back_populates="import_jobs")


class PersonaKnowledgeIndexJob(Base):
    """Durable outbox entry for one immutable published Persona version."""

    __tablename__ = "persona_knowledge_index_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_persona_knowledge_index_jobs_version"),
        CheckConstraint("status IN ('pending', 'indexing', 'ready', 'failed')",
                        name="ck_persona_knowledge_index_jobs_status"),
        Index("ix_persona_knowledge_index_jobs_status_next", "status", "next_attempt_at"),
        Index("ix_persona_knowledge_index_jobs_owner", "owner_user_id", "brokerage_id"),
    )

    id = Column(String(32), primary_key=True)
    persona_id = Column(Integer, ForeignKey("personas.id", ondelete="RESTRICT"), nullable=False, index=True)
    version_id = Column(Integer, ForeignKey("persona_versions.id", ondelete="RESTRICT"), nullable=False, index=True)
    owner_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    brokerage_id = Column(String(255), nullable=False, index=True)
    source_document_id = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False, default="pending", index=True)
    content_hash = Column(String(64), nullable=False)
    chunk_count = Column(Integer, nullable=False, default=0)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    worker_id = Column(String(100), nullable=True)
    error_message = Column(String(500), nullable=True)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    claimed_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


for _immutable_model, _message in (
    (PersonaSubmissionExample, "Submitted Persona examples are immutable"),
    (PersonaVersionExample, "Published Persona examples are immutable"),
):
    event.listen(_immutable_model, "before_update", lambda *_args, message=_message: (_ for _ in ()).throw(ValueError(message)), propagate=True)
    event.listen(_immutable_model, "before_delete", lambda *_args, message=_message: (_ for _ in ()).throw(ValueError(message)), propagate=True)


class PersonaDefault(Base):
    __tablename__ = "persona_defaults"
    __table_args__ = (
        UniqueConstraint("user_id", "channel", name="uq_persona_defaults_user_channel"),
        CheckConstraint(
            "channel IN ('sms', 'email')", name="ck_persona_defaults_channel"
        ),
        Index("ix_persona_defaults_brokerage_channel", "brokerage_id", "channel"),
    )

    id = Column(Integer, primary_key=True)
    brokerage_id = Column(String(255), nullable=False, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel = Column(String(20), nullable=False)
    persona_id = Column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    updated_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    persona = relationship("Persona", foreign_keys=[persona_id])


class PersonaEvent(Base):
    __tablename__ = "persona_events"
    __table_args__ = (
        Index("ix_persona_events_persona_created", "persona_id", "created_at"),
        Index("ix_persona_events_brokerage_type", "brokerage_id", "event_type"),
    )

    id = Column(Integer, primary_key=True)
    brokerage_id = Column(String(255), nullable=False, index=True)
    persona_id = Column(
        Integer,
        ForeignKey("personas.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    persona_version_id = Column(
        Integer,
        ForeignKey("persona_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type = Column(String(80), nullable=False, index=True)
    actor_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    from_state = Column(String(80), nullable=True)
    to_state = Column(String(80), nullable=True)
    detail = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    persona = relationship("Persona", foreign_keys=[persona_id])
    persona_version = relationship("PersonaVersion", foreign_keys=[persona_version_id])


# ---------------------------------------------------------------------------
# Brokerage membership trigger
# ---------------------------------------------------------------------------
# Brokerage changes happen outside the application (platform scripts and
# direct database edits), so they are detected by a trigger on users rather
# than by application code. Migration 0032 creates the same trigger; these
# listeners install it for metadata.create_all (tests and local SQLite).

MEMBERSHIP_SCOPE_SQL = (
    "owner_user_id = NEW.id AND membership_ended_at IS NULL"
    " AND (NEW.brokerage_id IS NULL OR brokerage_id <> NEW.brokerage_id)"
)

SQLITE_MEMBERSHIP_TRIGGER = f"""
CREATE TRIGGER trg_users_persona_membership_ended
AFTER UPDATE OF brokerage_id ON users
WHEN OLD.brokerage_id IS NOT NEW.brokerage_id
BEGIN
    INSERT INTO persona_events
        (brokerage_id, persona_id, event_type, actor_user_id, from_state, to_state,
         detail, created_at)
    SELECT brokerage_id, id, 'persona.owner_membership_ended', NULL, 'member',
           'former_member', '{{}}', CURRENT_TIMESTAMP
    FROM personas WHERE {MEMBERSHIP_SCOPE_SQL};
    UPDATE personas SET membership_ended_at = CURRENT_TIMESTAMP
    WHERE {MEMBERSHIP_SCOPE_SQL};
    DELETE FROM persona_defaults WHERE user_id = NEW.id;
END
"""

POSTGRES_MEMBERSHIP_FUNCTION = f"""
CREATE FUNCTION persona_end_owner_membership()
RETURNS trigger AS $$
BEGIN
    IF OLD.brokerage_id IS DISTINCT FROM NEW.brokerage_id THEN
        INSERT INTO persona_events
            (brokerage_id, persona_id, event_type, actor_user_id, from_state,
             to_state, detail, created_at)
        SELECT brokerage_id, id, 'persona.owner_membership_ended', NULL, 'member',
               'former_member', '{{}}'::json, timezone('utc', now())
        FROM personas WHERE {MEMBERSHIP_SCOPE_SQL};
        UPDATE personas SET membership_ended_at = timezone('utc', now())
        WHERE {MEMBERSHIP_SCOPE_SQL};
        DELETE FROM persona_defaults WHERE user_id = NEW.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

POSTGRES_MEMBERSHIP_TRIGGER = """
CREATE TRIGGER trg_users_persona_membership_ended
AFTER UPDATE OF brokerage_id ON users
FOR EACH ROW EXECUTE FUNCTION persona_end_owner_membership()
"""

# Installed after the last table the trigger writes to exists.
_events_table = PersonaEvent.__table__
event.listen(
    _events_table,
    "after_create",
    DDL(SQLITE_MEMBERSHIP_TRIGGER).execute_if(dialect="sqlite"),
)
event.listen(
    _events_table,
    "after_create",
    DDL(POSTGRES_MEMBERSHIP_FUNCTION).execute_if(dialect="postgresql"),
)
event.listen(
    _events_table,
    "after_create",
    DDL(POSTGRES_MEMBERSHIP_TRIGGER).execute_if(dialect="postgresql"),
)
event.listen(
    _events_table,
    "before_drop",
    DDL("DROP TRIGGER IF EXISTS trg_users_persona_membership_ended").execute_if(
        dialect="sqlite"
    ),
)
event.listen(
    _events_table,
    "before_drop",
    DDL(
        "DROP TRIGGER IF EXISTS trg_users_persona_membership_ended ON users;"
        " DROP FUNCTION IF EXISTS persona_end_owner_membership()"
    ).execute_if(dialect="postgresql"),
)
