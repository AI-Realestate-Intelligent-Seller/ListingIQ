"""Add the Personal AI Persona foundation.

This migration is schema-only and additive. It does not seed Bobbie, bind
conversations, invoke AI providers, touch vector storage, or alter SMS
delivery, campaign or queue tables.

Retention rules enforced in the database:

* ``persona_versions`` rows can never be updated or deleted (trigger), and a
  Persona with versions cannot be deleted (``RESTRICT``).
* ``persona_submissions`` review snapshots are immutable, a reviewed
  submission's decision is final, and submissions are never deleted.
* At most one submission per Persona awaits review (partial unique index).
* When a user's ``brokerage_id`` changes by any route (application, platform
  script or direct SQL), a trigger on ``users`` stamps that user's Personas
  outside the new brokerage with ``membership_ended_at``, removes their
  defaults and records an audit event. The trigger never changes ``users``.
  A stamped Persona is never reattached, even if the user rejoins.
"""

import sqlalchemy as sa

from alembic import op

revision = "0032_persona_core"
down_revision = "0031_campaign_draft_groups"
branch_labels = None
depends_on = None


# The SQL is copied here, not imported from app code, so this migration stays
# stable if the application module changes later. Keep it in step with
# app/personas/models.py.
_MEMBERSHIP_SCOPE = (
    "owner_user_id = NEW.id AND membership_ended_at IS NULL"
    " AND (NEW.brokerage_id IS NULL OR brokerage_id <> NEW.brokerage_id)"
)


def _create_membership_trigger(dialect):
    if dialect == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION persona_end_owner_membership()
            RETURNS trigger AS $$
            BEGIN
                IF OLD.brokerage_id IS DISTINCT FROM NEW.brokerage_id THEN
                    INSERT INTO persona_events
                        (brokerage_id, persona_id, event_type, actor_user_id,
                         from_state, to_state, detail, created_at)
                    SELECT brokerage_id, id, 'persona.owner_membership_ended', NULL,
                           'member', 'former_member', '{{}}'::json,
                           timezone('utc', now())
                    FROM personas WHERE {_MEMBERSHIP_SCOPE};
                    UPDATE personas SET membership_ended_at = timezone('utc', now())
                    WHERE {_MEMBERSHIP_SCOPE};
                    DELETE FROM persona_defaults WHERE user_id = NEW.id;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_users_persona_membership_ended
            AFTER UPDATE OF brokerage_id ON users
            FOR EACH ROW EXECUTE FUNCTION persona_end_owner_membership()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            f"""
            CREATE TRIGGER trg_users_persona_membership_ended
            AFTER UPDATE OF brokerage_id ON users
            WHEN OLD.brokerage_id IS NOT NEW.brokerage_id
            BEGIN
                INSERT INTO persona_events
                    (brokerage_id, persona_id, event_type, actor_user_id,
                     from_state, to_state, detail, created_at)
                SELECT brokerage_id, id, 'persona.owner_membership_ended', NULL,
                       'member', 'former_member', '{{}}', CURRENT_TIMESTAMP
                FROM personas WHERE {_MEMBERSHIP_SCOPE};
                UPDATE personas SET membership_ended_at = CURRENT_TIMESTAMP
                WHERE {_MEMBERSHIP_SCOPE};
                DELETE FROM persona_defaults WHERE user_id = NEW.id;
            END
            """
        )


def upgrade():
    op.create_table(
        "personas",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brokerage_id", sa.String(length=255), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("membership_ended_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        # One user's own speaking identity; no brokerage-wide speaking Persona.
        sa.CheckConstraint("scope = 'personal'", name="ck_personas_scope"),
        sa.CheckConstraint(
            "status IN ('active', 'archived')", name="ck_personas_status"
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_personas_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_personas_created_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("brokerage_id", "slug", name="uq_personas_brokerage_slug"),
    )
    op.create_index("ix_personas_brokerage_id", "personas", ["brokerage_id"])
    op.create_index("ix_personas_owner_user_id", "personas", ["owner_user_id"])
    op.create_index("ix_personas_status", "personas", ["status"])
    op.create_index(
        "ix_personas_created_by_user_id", "personas", ["created_by_user_id"]
    )
    op.create_index(
        "ix_personas_membership_ended_at", "personas", ["membership_ended_at"]
    )
    op.create_index(
        "ix_personas_brokerage_owner_status",
        "personas",
        ["brokerage_id", "owner_user_id", "status"],
    )

    op.create_table(
        "persona_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("professional_role", sa.String(length=120), nullable=False),
        sa.Column("professional_title", sa.String(length=160), nullable=True),
        sa.Column("brokerage_name", sa.String(length=255), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        sa.Column("experience_summary", sa.Text(), nullable=False),
        sa.Column("specialties", sa.JSON(), nullable=False),
        sa.Column("service_areas", sa.JSON(), nullable=False),
        sa.Column("sms_signature", sa.String(length=320), nullable=False),
        sa.Column("style_config", sa.JSON(), nullable=False),
        sa.Column("style_instructions", sa.Text(), nullable=False),
        sa.Column("preferred_language", sa.String(length=40), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("style_schema_version", sa.Integer(), nullable=False),
        sa.Column("prompt_schema_version", sa.Integer(), nullable=False),
        sa.Column("published_by_user_id", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version_number > 0", name="ck_persona_versions_positive_number"
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_versions_persona_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_user_id"],
            ["users.id"],
            name="fk_persona_versions_published_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "persona_id", "version_number", name="uq_persona_versions_number"
        ),
        sa.UniqueConstraint(
            "persona_id", "content_hash", name="uq_persona_versions_content_hash"
        ),
    )
    op.create_index(
        "ix_persona_versions_persona_id", "persona_versions", ["persona_id"]
    )
    op.create_index(
        "ix_persona_versions_published_by_user_id",
        "persona_versions",
        ["published_by_user_id"],
    )
    op.create_index(
        "ix_persona_versions_persona_published",
        "persona_versions",
        ["persona_id", "published_at"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_persona_version_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Published Persona versions are immutable and retained';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_persona_versions_immutable
            BEFORE UPDATE OR DELETE ON persona_versions
            FOR EACH ROW EXECUTE FUNCTION reject_persona_version_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_persona_versions_immutable
            BEFORE UPDATE ON persona_versions
            BEGIN
                SELECT RAISE(ABORT, 'Published Persona versions are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_persona_versions_retain
            BEFORE DELETE ON persona_versions
            BEGIN
                SELECT RAISE(ABORT, 'Published Persona versions must be retained');
            END
            """
        )

    with op.batch_alter_table("personas") as batch:
        batch.add_column(sa.Column("active_version_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_personas_active_version_id",
            "persona_versions",
            ["active_version_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_personas_active_version_id", ["active_version_id"])

    op.create_table(
        "persona_submissions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("submission_number", sa.Integer(), nullable=False),
        sa.Column("draft_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=500), nullable=True),
        sa.Column("published_version_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "submission_number > 0",
            name="ck_persona_submissions_positive_number",
        ),
        sa.CheckConstraint(
            "draft_revision > 0",
            name="ck_persona_submissions_positive_revision",
        ),
        sa.CheckConstraint(
            "status IN ('submitted', 'published', 'rejected')",
            name="ck_persona_submissions_status",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_submissions_persona_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            name="fk_persona_submissions_submitted_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            name="fk_persona_submissions_reviewed_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["published_version_id"],
            ["persona_versions.id"],
            name="fk_persona_submissions_published_version_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "persona_id",
            "submission_number",
            name="uq_persona_submissions_number",
        ),
        sa.UniqueConstraint(
            "persona_id",
            "draft_revision",
            name="uq_persona_submissions_draft_revision",
        ),
    )
    op.create_index(
        "ix_persona_submissions_persona_id", "persona_submissions", ["persona_id"]
    )
    op.create_index(
        "ix_persona_submissions_status", "persona_submissions", ["status"]
    )
    op.create_index(
        "ix_persona_submissions_submitted_by_user_id",
        "persona_submissions",
        ["submitted_by_user_id"],
    )
    op.create_index(
        "ix_persona_submissions_reviewed_by_user_id",
        "persona_submissions",
        ["reviewed_by_user_id"],
    )
    op.create_index(
        "ix_persona_submissions_published_version_id",
        "persona_submissions",
        ["published_version_id"],
    )
    op.create_index(
        "ix_persona_submissions_persona_status",
        "persona_submissions",
        ["persona_id", "status"],
    )
    op.create_index(
        "uq_persona_submissions_one_pending",
        "persona_submissions",
        ["persona_id"],
        unique=True,
        sqlite_where=sa.text("status = 'submitted'"),
        postgresql_where=sa.text("status = 'submitted'"),
    )
    if dialect == "postgresql":
        # snapshot is JSON (no equality operator), so compare its text form.
        op.execute(
            """
            CREATE FUNCTION reject_persona_submission_mutation()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'Persona review submissions must be retained';
                END IF;
                IF OLD.persona_id IS DISTINCT FROM NEW.persona_id
                   OR OLD.submission_number IS DISTINCT FROM NEW.submission_number
                   OR OLD.draft_revision IS DISTINCT FROM NEW.draft_revision
                   OR OLD.snapshot::text IS DISTINCT FROM NEW.snapshot::text
                   OR OLD.content_hash IS DISTINCT FROM NEW.content_hash
                   OR OLD.submitted_by_user_id IS DISTINCT FROM NEW.submitted_by_user_id
                   OR OLD.submitted_at IS DISTINCT FROM NEW.submitted_at THEN
                    RAISE EXCEPTION 'Submitted Persona review snapshots are immutable';
                END IF;
                IF OLD.status <> 'submitted' AND (
                       OLD.status IS DISTINCT FROM NEW.status
                       OR OLD.reviewed_by_user_id IS DISTINCT FROM NEW.reviewed_by_user_id
                       OR OLD.reviewed_at IS DISTINCT FROM NEW.reviewed_at
                       OR OLD.rejection_reason IS DISTINCT FROM NEW.rejection_reason
                       OR OLD.published_version_id IS DISTINCT FROM NEW.published_version_id
                   ) THEN
                    RAISE EXCEPTION 'A reviewed Persona submission cannot change again';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_persona_submissions_immutable
            BEFORE UPDATE OR DELETE ON persona_submissions
            FOR EACH ROW EXECUTE FUNCTION reject_persona_submission_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER trg_persona_submissions_snapshot_immutable
            BEFORE UPDATE OF persona_id, submission_number, draft_revision, snapshot,
                content_hash, submitted_by_user_id, submitted_at
            ON persona_submissions
            BEGIN
                SELECT RAISE(ABORT, 'Submitted Persona review snapshots are immutable');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_persona_submissions_decision_final
            BEFORE UPDATE OF status, reviewed_by_user_id, reviewed_at,
                rejection_reason, published_version_id
            ON persona_submissions
            WHEN OLD.status <> 'submitted'
            BEGIN
                SELECT RAISE(ABORT, 'A reviewed Persona submission cannot change again');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_persona_submissions_retain
            BEFORE DELETE ON persona_submissions
            BEGIN
                SELECT RAISE(ABORT, 'Persona review submissions must be retained');
            END
            """
        )

    op.create_table(
        "persona_drafts",
        sa.Column("persona_id", sa.Integer(), primary_key=True),
        sa.Column("base_version_id", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("professional_role", sa.String(length=120), nullable=False),
        sa.Column("professional_title", sa.String(length=160), nullable=True),
        sa.Column("brokerage_name", sa.String(length=255), nullable=False),
        sa.Column("biography", sa.Text(), nullable=False),
        sa.Column("experience_summary", sa.Text(), nullable=False),
        sa.Column("specialties", sa.JSON(), nullable=False),
        sa.Column("service_areas", sa.JSON(), nullable=False),
        sa.Column("sms_signature", sa.String(length=320), nullable=False),
        sa.Column("style_config", sa.JSON(), nullable=False),
        sa.Column("style_instructions", sa.Text(), nullable=False),
        sa.Column("preferred_language", sa.String(length=40), nullable=False),
        sa.Column("review_status", sa.String(length=20), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("last_editor_user_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_persona_drafts_positive_revision"),
        sa.CheckConstraint(
            "review_status IN ('draft', 'submitted', 'published')",
            name="ck_persona_drafts_review_status",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_drafts_persona_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["base_version_id"],
            ["persona_versions.id"],
            name="fk_persona_drafts_base_version_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["users.id"],
            name="fk_persona_drafts_submitted_by_user_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_editor_user_id"],
            ["users.id"],
            name="fk_persona_drafts_last_editor_user_id",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_persona_drafts_base_version_id", "persona_drafts", ["base_version_id"]
    )
    op.create_index(
        "ix_persona_drafts_review_status", "persona_drafts", ["review_status"]
    )
    op.create_index(
        "ix_persona_drafts_last_editor_user_id",
        "persona_drafts",
        ["last_editor_user_id"],
    )

    op.create_table(
        "persona_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brokerage_id", sa.String(length=255), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "channel IN ('sms', 'email')", name="ck_persona_defaults_channel"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_persona_defaults_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_defaults_persona_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_user_id"],
            ["users.id"],
            name="fk_persona_defaults_updated_by_user_id",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "user_id", "channel", name="uq_persona_defaults_user_channel"
        ),
    )
    op.create_index(
        "ix_persona_defaults_brokerage_id", "persona_defaults", ["brokerage_id"]
    )
    op.create_index("ix_persona_defaults_user_id", "persona_defaults", ["user_id"])
    op.create_index(
        "ix_persona_defaults_persona_id", "persona_defaults", ["persona_id"]
    )
    op.create_index(
        "ix_persona_defaults_brokerage_channel",
        "persona_defaults",
        ["brokerage_id", "channel"],
    )

    op.create_table(
        "persona_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brokerage_id", sa.String(length=255), nullable=False),
        sa.Column("persona_id", sa.Integer(), nullable=False),
        sa.Column("persona_version_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("from_state", sa.String(length=80), nullable=True),
        sa.Column("to_state", sa.String(length=80), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["persona_id"],
            ["personas.id"],
            name="fk_persona_events_persona_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["persona_version_id"],
            ["persona_versions.id"],
            name="fk_persona_events_persona_version_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_persona_events_actor_user_id",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_persona_events_brokerage_id", "persona_events", ["brokerage_id"]
    )
    op.create_index("ix_persona_events_persona_id", "persona_events", ["persona_id"])
    op.create_index(
        "ix_persona_events_persona_version_id", "persona_events", ["persona_version_id"]
    )
    op.create_index("ix_persona_events_event_type", "persona_events", ["event_type"])
    op.create_index(
        "ix_persona_events_actor_user_id", "persona_events", ["actor_user_id"]
    )
    op.create_index("ix_persona_events_created_at", "persona_events", ["created_at"])
    op.create_index(
        "ix_persona_events_persona_created",
        "persona_events",
        ["persona_id", "created_at"],
    )
    op.create_index(
        "ix_persona_events_brokerage_type",
        "persona_events",
        ["brokerage_id", "event_type"],
    )

    _create_membership_trigger(dialect)

def downgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER trg_users_persona_membership_ended ON users")
        op.execute("DROP FUNCTION persona_end_owner_membership()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER trg_users_persona_membership_ended")
    op.drop_table("persona_events")
    op.drop_table("persona_defaults")
    op.drop_table("persona_drafts")
    if dialect == "postgresql":
        op.execute("DROP TRIGGER trg_persona_submissions_immutable ON persona_submissions")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER trg_persona_submissions_snapshot_immutable")
        op.execute("DROP TRIGGER trg_persona_submissions_decision_final")
        op.execute("DROP TRIGGER trg_persona_submissions_retain")
    op.drop_index("uq_persona_submissions_one_pending", table_name="persona_submissions")
    op.drop_table("persona_submissions")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION reject_persona_submission_mutation()")
    with op.batch_alter_table("personas") as batch:
        batch.drop_index("ix_personas_active_version_id")
        batch.drop_constraint("fk_personas_active_version_id", type_="foreignkey")
        batch.drop_column("active_version_id")
    if dialect == "postgresql":
        op.execute("DROP TRIGGER trg_persona_versions_immutable ON persona_versions")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER trg_persona_versions_immutable")
        op.execute("DROP TRIGGER trg_persona_versions_retain")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION reject_persona_version_mutation()")
    op.drop_table("persona_versions")
    op.drop_table("personas")
