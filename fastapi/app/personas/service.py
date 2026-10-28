"""Persona draft, publication, activation and default-management service."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import User
from . import permissions
from .models import (
    Persona,
    PersonaDefault,
    PersonaDraft,
    PersonaEvent,
    PersonaSubmission,
    PersonaVersion,
)
from .schemas import CommunicationStyle, PersonaCreate, PersonaDraftUpdate


class PersonaConflict(RuntimeError):
    pass


class PersonaValidationFailure(RuntimeError):
    def __init__(self, message: str, errors: list[str]):
        super().__init__(message)
        self.errors = errors


def _role_label(role: str) -> str:
    return {
        "hob": "Head of Brokerage",
        "broker": "Broker",
        "agent": "Agent",
    }.get(role, "Real Estate Professional")


_DISPLAY_NAME_MAX = 120
_AI_SUFFIX = " AI"


def _display_name(full_name: str) -> str:
    """Derive "<First name> AI" for any script: José García -> José AI.

    Letters, combining marks and digits of every script are kept so names in
    Devanagari, Arabic, CJK and others stay intact.
    """
    parts = unicodedata.normalize("NFC", full_name).split()
    first = "".join(
        character
        for character in (parts[0] if parts else "")
        if unicodedata.category(character)[0] in {"L", "M", "N"} or character in "'-’"
    ).strip("'-’")
    first = first[: _DISPLAY_NAME_MAX - len(_AI_SUFFIX)]
    return f"{first or 'My'}{_AI_SUFFIX}"


def _slug(value: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    )
    cleaned = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return cleaned[:100].strip("-") or "persona"


def _unique_slug(
    session: Session, brokerage_id: str, requested: str, owner_id: int
) -> str:
    # Former memberships keep their slugs, so a returning member may need a
    # numbered suffix.
    base = _slug(requested)
    candidates = [base, f"{base[:88]}-{owner_id}"] + [
        f"{base[:80]}-{owner_id}-{number}" for number in range(2, 21)
    ]
    taken = {
        row[0]
        for row in session.query(Persona.slug).filter(
            Persona.brokerage_id == brokerage_id, Persona.slug.in_(candidates)
        )
    }
    for candidate in candidates:
        if candidate not in taken:
            return candidate
    raise PersonaConflict("A Persona with this slug already exists in the brokerage")


def _record_event(
    session: Session,
    persona: Persona,
    actor: User,
    event_type: str,
    *,
    version: PersonaVersion | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    detail: dict | None = None,
) -> PersonaEvent:
    row = PersonaEvent(
        brokerage_id=persona.brokerage_id,
        persona_id=persona.id,
        persona_version_id=version.id if version else None,
        event_type=event_type,
        actor_user_id=actor.id,
        from_state=from_state,
        to_state=to_state,
        detail=detail or {},
        created_at=datetime.utcnow(),
    )
    session.add(row)
    return row


def _not_found(message: str = "Persona not found") -> permissions.PersonaAccessError:
    return permissions.PersonaAccessError(message, not_found=True)


def _load(
    session: Session,
    actor: User,
    persona_id: int,
    *,
    owner: bool = False,
    lock: bool = False,
) -> Persona:
    query = session.query(Persona).filter(Persona.id == persona_id)
    if lock:
        query = query.with_for_update()
    persona = query.first()
    if persona is None:
        raise _not_found()
    if owner:
        permissions.require_owner(actor, persona)
    else:
        permissions.require_view(actor, persona)
    return persona


def list_accessible(
    session: Session, actor: User, include_archived: bool = False
) -> list[Persona]:
    permissions.require_member(actor)
    query = session.query(Persona).filter(*permissions.visible_filter(actor))
    if not include_archived:
        query = query.filter(Persona.status == "active")
    return query.order_by(Persona.display_name, Persona.id).all()


def create(session: Session, actor: User, payload: PersonaCreate) -> Persona:
    # The owner and brokerage always come from the authenticated user.
    permissions.require_member(actor)
    owner = actor
    display_name = payload.display_name or _display_name(payload.full_name)
    slug = _unique_slug(
        session,
        actor.brokerage_id,
        payload.slug or display_name,
        owner.id,
    )
    persona = Persona(
        brokerage_id=actor.brokerage_id,
        owner_user_id=owner.id,
        slug=slug,
        display_name=display_name,
        scope="personal",
        status="active",
        created_by_user_id=actor.id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(persona)
    session.flush()
    persona.draft = PersonaDraft(
        persona_id=persona.id,
        revision=1,
        full_name=payload.full_name,
        display_name=display_name,
        professional_role=_role_label(owner.role),
        professional_title=None,
        brokerage_name=owner.brokerage_name or actor.brokerage_name or "",
        biography="",
        experience_summary="",
        specialties=[],
        service_areas=[],
        sms_signature=payload.full_name,
        style_config=CommunicationStyle().dict(),
        style_instructions="",
        preferred_language="en",
        review_status="draft",
        last_editor_user_id=actor.id,
        updated_at=datetime.utcnow(),
    )
    _record_event(
        session,
        persona,
        actor,
        "persona.created",
        to_state="active",
        detail={"scope": persona.scope, "owner_user_id": owner.id},
    )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise PersonaConflict(
            "A Persona with this slug already exists in the brokerage"
        ) from error
    session.refresh(persona)
    return persona


def get(session: Session, actor: User, persona_id: int) -> Persona:
    return _load(session, actor, persona_id)


def get_draft(session: Session, actor: User, persona_id: int) -> PersonaDraft:
    persona = _load(session, actor, persona_id, owner=True)
    return persona.draft


def update_draft(
    session: Session, actor: User, persona_id: int, payload: PersonaDraftUpdate
) -> PersonaDraft:
    persona = _load(session, actor, persona_id, owner=True)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be edited")
    draft = persona.draft
    if draft.revision != payload.revision:
        raise PersonaConflict(
            f"Draft revision is stale; expected {draft.revision}, received {payload.revision}"
        )
    changes = {
        field: getattr(payload, field)
        for field in payload.__fields_set__
        if field != "revision"
    }
    if not changes:
        raise PersonaValidationFailure("No draft changes were supplied", ["no_changes"])
    changed_fields = []
    values = {}
    for field, value in changes.items():
        if field == "style_config":
            # The style object is replaced whole, with defaults for omitted keys.
            value = value.dict()
        if getattr(draft, field) != value:
            values[field] = value
            changed_fields.append(field)
    if not changed_fields:
        return draft
    next_revision = payload.revision + 1
    values.update(
        {
            "revision": next_revision,
            "review_status": "draft",
            "submitted_by_user_id": None,
            "submitted_at": None,
            "last_editor_user_id": actor.id,
            "updated_at": datetime.utcnow(),
        }
    )
    # Compare-and-swap: the database applies the change only while the row
    # still has the revision the client edited. Two tabs sending the same
    # revision race on this single statement and exactly one wins.
    result = session.execute(
        update(PersonaDraft)
        .where(
            PersonaDraft.persona_id == persona.id,
            PersonaDraft.revision == payload.revision,
            PersonaDraft.persona_id.in_(
                select(Persona.id).where(
                    Persona.id == persona.id, Persona.status == "active"
                )
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise PersonaConflict("Draft revision is stale; reload the draft and retry")
    session.expire(draft)
    session.refresh(draft)
    _record_event(
        session,
        persona,
        actor,
        "persona.draft_changed",
        detail={"revision": next_revision, "fields": sorted(changed_fields)},
    )
    session.commit()
    session.refresh(draft)
    return draft


def validation_result(draft: PersonaDraft) -> dict:
    required = {
        "full_name": draft.full_name,
        "display_name": draft.display_name,
        "professional_role": draft.professional_role,
        "brokerage_name": draft.brokerage_name,
        "sms_signature": draft.sms_signature,
        "preferred_language": draft.preferred_language,
    }
    errors = [
        f"{field} is required"
        for field, value in required.items()
        if not str(value or "").strip()
    ]
    try:
        CommunicationStyle.parse_obj(draft.style_config or {})
    except ValidationError:
        errors.append("communication style configuration is invalid")
    warnings = []
    if not draft.biography.strip():
        warnings.append("biography is empty")
    if not draft.experience_summary.strip():
        warnings.append("experience summary is empty")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "revision": draft.revision,
    }


def validate(session: Session, actor: User, persona_id: int) -> dict:
    return validation_result(get_draft(session, actor, persona_id))


def submit(
    session: Session, actor: User, persona_id: int, revision: int
) -> PersonaSubmission:
    """Freeze one specific draft revision as immutable HOB review material."""
    persona = _load(session, actor, persona_id, owner=True)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be submitted")
    draft = persona.draft
    if draft.revision != revision:
        raise PersonaConflict(
            f"Draft revision is stale; current revision is {draft.revision}"
        )
    if (
        session.query(PersonaSubmission.id)
        .filter(
            PersonaSubmission.persona_id == persona.id,
            PersonaSubmission.draft_revision == revision,
        )
        .first()
    ):
        raise PersonaConflict(
            "This draft revision was already submitted; edit the draft to resubmit"
        )
    result = validation_result(draft)
    if not result["valid"]:
        raise PersonaValidationFailure("Persona draft is not valid", result["errors"])
    pending = (
        session.query(PersonaSubmission.id)
        .filter(
            PersonaSubmission.persona_id == persona.id,
            PersonaSubmission.status == "submitted",
        )
        .first()
    )
    if pending:
        raise PersonaConflict("This Persona already has a submission awaiting review")
    snapshot = _snapshot(draft)
    submission_number = (
        session.query(func.max(PersonaSubmission.submission_number))
        .filter(PersonaSubmission.persona_id == persona.id)
        .scalar()
        or 0
    ) + 1
    submission = PersonaSubmission(
        persona_id=persona.id,
        submission_number=submission_number,
        draft_revision=revision,
        snapshot=snapshot,
        content_hash=_content_hash(snapshot),
        status="submitted",
        submitted_by_user_id=actor.id,
        submitted_at=datetime.utcnow(),
    )
    session.add(submission)
    previous = draft.review_status
    now = datetime.utcnow()
    # Mark the draft as submitted only if it is still the submitted revision;
    # a concurrent edit from another tab turns this request into a 409.
    marked = session.execute(
        update(PersonaDraft)
        .where(
            PersonaDraft.persona_id == persona.id,
            PersonaDraft.revision == revision,
        )
        .values(
            review_status="submitted",
            submitted_by_user_id=actor.id,
            submitted_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if marked.rowcount != 1:
        session.rollback()
        raise PersonaConflict("Draft changed while it was being submitted; retry")
    _record_event(
        session,
        persona,
        actor,
        "persona.draft_submitted",
        from_state=previous,
        to_state="submitted",
        detail={"revision": revision},
    )
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise PersonaConflict(
            "This Persona already has a submission awaiting review"
        ) from error
    session.commit()
    session.refresh(submission)
    return submission


def submissions(
    session: Session, actor: User, persona_id: int
) -> list[PersonaSubmission]:
    persona = _load(session, actor, persona_id)
    permissions.require_submission_access(session, actor, persona)
    return (
        session.query(PersonaSubmission)
        .filter(PersonaSubmission.persona_id == persona.id)
        .order_by(PersonaSubmission.submission_number.desc())
        .all()
    )


def submission(
    session: Session, actor: User, persona_id: int, submission_id: int
) -> PersonaSubmission:
    persona = _load(session, actor, persona_id)
    permissions.require_submission_access(session, actor, persona)
    row = (
        session.query(PersonaSubmission)
        .filter(
            PersonaSubmission.id == submission_id,
            PersonaSubmission.persona_id == persona.id,
        )
        .first()
    )
    if row is None:
        raise permissions.PersonaAccessError(
            "Persona submission not found", not_found=True
        )
    return row


def reject_submission(
    session: Session,
    actor: User,
    persona_id: int,
    submission_id: int,
    reason: str,
) -> PersonaSubmission:
    persona = (
        session.query(Persona)
        .filter(Persona.id == persona_id)
        .with_for_update()
        .first()
    )
    if persona is None:
        raise permissions.PersonaAccessError("Persona not found", not_found=True)
    permissions.require_reviewer(session, actor, persona)
    row = (
        session.query(PersonaSubmission)
        .filter(
            PersonaSubmission.id == submission_id,
            PersonaSubmission.persona_id == persona.id,
        )
        .with_for_update()
        .first()
    )
    if row is None:
        raise permissions.PersonaAccessError(
            "Persona submission not found", not_found=True
        )
    if row.status != "submitted":
        raise PersonaConflict("Only a pending submission can be rejected")
    row.status = "rejected"
    row.reviewed_by_user_id = actor.id
    row.reviewed_at = datetime.utcnow()
    row.rejection_reason = reason
    _mark_draft_if_revision(
        session, persona.id, row.draft_revision, review_status="draft"
    )
    _record_event(
        session,
        persona,
        actor,
        "persona.submission_rejected",
        from_state="submitted",
        to_state="rejected",
        detail={"submission_id": row.id, "revision": row.draft_revision},
    )
    session.commit()
    session.refresh(row)
    return row


_SNAPSHOT_FIELDS = (
    "full_name",
    "display_name",
    "professional_role",
    "professional_title",
    "brokerage_name",
    "biography",
    "experience_summary",
    "specialties",
    "service_areas",
    "sms_signature",
    "style_config",
    "style_instructions",
    "preferred_language",
)


def _mark_draft_if_revision(
    session: Session, persona_id: int, revision: int, **values
) -> None:
    """Update review bookkeeping only while the draft is still that revision.

    The reviewer never writes draft content, and a newer owner edit is left
    untouched.
    """
    session.execute(
        update(PersonaDraft)
        .where(
            PersonaDraft.persona_id == persona_id,
            PersonaDraft.revision == revision,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )


def _snapshot(draft: PersonaDraft) -> dict:
    return {field: getattr(draft, field) for field in _SNAPSHOT_FIELDS}


def _content_hash(snapshot: dict) -> str:
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def publish(
    session: Session, actor: User, persona_id: int, submission_id: int
) -> PersonaVersion:
    persona = (
        session.query(Persona)
        .filter(Persona.id == persona_id)
        .with_for_update()
        .first()
    )
    if persona is None:
        raise permissions.PersonaAccessError("Persona not found", not_found=True)
    permissions.require_publish(session, actor, persona)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be published")
    submission = (
        session.query(PersonaSubmission)
        .filter(
            PersonaSubmission.id == submission_id,
            PersonaSubmission.persona_id == persona.id,
        )
        .with_for_update()
        .first()
    )
    if submission is None:
        raise permissions.PersonaAccessError(
            "Persona submission not found", not_found=True
        )
    if submission.status != "submitted":
        raise PersonaConflict("Only a pending submission can be published")
    snapshot = dict(submission.snapshot)
    content_hash = submission.content_hash
    if (
        set(snapshot) != set(_SNAPSHOT_FIELDS)
        or _content_hash(snapshot) != content_hash
    ):
        raise PersonaConflict("Submission snapshot failed its integrity check")
    if (
        session.query(PersonaVersion.id)
        .filter(
            PersonaVersion.persona_id == persona.id,
            PersonaVersion.content_hash == content_hash,
        )
        .first()
    ):
        raise PersonaConflict("This exact Persona configuration is already published")
    next_number = (
        session.query(func.max(PersonaVersion.version_number))
        .filter(PersonaVersion.persona_id == persona.id)
        .scalar()
        or 0
    ) + 1
    version = PersonaVersion(
        persona_id=persona.id,
        version_number=next_number,
        **snapshot,
        content_hash=content_hash,
        style_schema_version=1,
        prompt_schema_version=1,
        published_by_user_id=actor.id,
        published_at=datetime.utcnow(),
    )
    session.add(version)
    try:
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise PersonaConflict(
            "Persona publication conflicted with another publication"
        ) from error
    submission.status = "published"
    submission.reviewed_by_user_id = actor.id
    submission.reviewed_at = datetime.utcnow()
    submission.published_version_id = version.id
    _mark_draft_if_revision(
        session,
        persona.id,
        submission.draft_revision,
        base_version_id=version.id,
        review_status="published",
    )
    _record_event(
        session,
        persona,
        actor,
        "persona.version_published",
        version=version,
        to_state=f"v{version.version_number}",
        detail={
            "submission_id": submission.id,
            "revision": submission.draft_revision,
            "content_hash": content_hash,
        },
    )
    session.commit()
    session.refresh(version)
    return version


def versions(session: Session, actor: User, persona_id: int) -> list[PersonaVersion]:
    persona = _load(session, actor, persona_id)
    return (
        session.query(PersonaVersion)
        .filter(PersonaVersion.persona_id == persona.id)
        .order_by(PersonaVersion.version_number.desc())
        .all()
    )


def version(
    session: Session, actor: User, persona_id: int, version_number: int
) -> PersonaVersion:
    persona = _load(session, actor, persona_id)
    row = (
        session.query(PersonaVersion)
        .filter(
            PersonaVersion.persona_id == persona.id,
            PersonaVersion.version_number == version_number,
        )
        .first()
    )
    if row is None:
        raise permissions.PersonaAccessError(
            "Persona version not found", not_found=True
        )
    return row


def activate(
    session: Session, actor: User, persona_id: int, version_number: int
) -> PersonaVersion:
    persona = (
        session.query(Persona)
        .filter(Persona.id == persona_id)
        .with_for_update()
        .first()
    )
    if persona is None:
        raise permissions.PersonaAccessError("Persona not found", not_found=True)
    permissions.require_publish(session, actor, persona)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be activated")
    selected = (
        session.query(PersonaVersion)
        .filter(
            PersonaVersion.persona_id == persona.id,
            PersonaVersion.version_number == version_number,
        )
        .first()
    )
    if selected is None:
        raise permissions.PersonaAccessError(
            "Persona version not found", not_found=True
        )
    previous = persona.active_version
    if previous and previous.id == selected.id:
        return selected
    restored = (
        previous is not None and selected.version_number < previous.version_number
    )
    persona.active_version_id = selected.id
    persona.display_name = selected.display_name
    persona.updated_at = datetime.utcnow()
    _record_event(
        session,
        persona,
        actor,
        "persona.version_restored" if restored else "persona.version_activated",
        version=selected,
        from_state=f"v{previous.version_number}" if previous else None,
        to_state=f"v{selected.version_number}",
    )
    session.commit()
    session.refresh(selected)
    return selected


def archive(session: Session, actor: User, persona_id: int) -> Persona:
    persona = _load(session, actor, persona_id, lock=True)
    if actor.role != "hob":
        raise permissions.PersonaAccessError(
            "Only a Head of Brokerage can archive a Persona"
        )
    if persona.status == "archived":
        raise PersonaConflict("Persona is already archived")
    persona.status = "archived"
    persona.updated_at = datetime.utcnow()
    session.query(PersonaDefault).filter(
        PersonaDefault.persona_id == persona.id
    ).delete(synchronize_session=False)
    _record_event(
        session,
        persona,
        actor,
        "persona.archived",
        from_state="active",
        to_state="archived",
    )
    session.commit()
    session.refresh(persona)
    return persona


def _channel(value: str) -> str:
    channel = value.strip().lower()
    if channel not in {"sms", "email"}:
        raise PersonaValidationFailure("Unsupported Persona channel", ["channel"])
    return channel


def get_default(
    session: Session, actor: User, channel: str, target_user_id: int | None = None
) -> tuple[User, PersonaDefault | None]:
    """Resolve a user's own default; anything stale resolves to no default.

    Every lookup re-checks the user's current brokerage, ownership, archived
    status and the active published version, so a default set in a previous
    brokerage (or for a Persona since archived) never discloses that Persona.
    """
    channel = _channel(channel)
    target = permissions.resolve_default_target(
        session, actor, target_user_id, write=False
    )
    row = (
        session.query(PersonaDefault)
        .filter(
            PersonaDefault.user_id == target.id,
            PersonaDefault.channel == channel,
            PersonaDefault.brokerage_id == target.brokerage_id,
        )
        .first()
    )
    if row is not None and not _usable_default(target, row.persona):
        row = None
    return target, row


def _usable_default(target: User, persona: Persona | None) -> bool:
    if persona is None or not permissions.can_use(target, persona):
        return False
    active = persona.active_version
    return (
        persona.status == "active"
        and active is not None
        and active.persona_id == persona.id
    )


def set_default(
    session: Session,
    actor: User,
    channel: str,
    persona_id: int | None,
    target_user_id: int | None = None,
) -> tuple[User, PersonaDefault | None]:
    channel = _channel(channel)
    target = permissions.resolve_default_target(
        session, actor, target_user_id, write=True
    )
    existing = (
        session.query(PersonaDefault)
        .filter(
            PersonaDefault.user_id == target.id,
            PersonaDefault.channel == channel,
        )
        .first()
    )
    if persona_id is None:
        if existing is not None:
            persona = existing.persona
            session.delete(existing)
            if permissions.can_use(target, persona):
                _record_event(
                    session,
                    persona,
                    actor,
                    "persona.default_changed",
                    from_state=channel,
                    to_state=None,
                    detail={"target_user_id": target.id},
                )
            session.commit()
        return target, None
    persona = session.get(Persona, persona_id)
    if persona is None or not permissions.can_use(target, persona):
        raise permissions.PersonaAccessError(
            "Persona is not accessible to the default user", not_found=True
        )
    if not _usable_default(target, persona):
        raise PersonaConflict(
            "Only an active Persona with a published active version can be a default"
        )
    if existing is None:
        existing = PersonaDefault(
            brokerage_id=target.brokerage_id,
            user_id=target.id,
            channel=channel,
            persona_id=persona.id,
            updated_by_user_id=actor.id,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        session.add(existing)
    else:
        existing.brokerage_id = target.brokerage_id
        existing.persona_id = persona.id
        existing.updated_by_user_id = actor.id
        existing.updated_at = datetime.utcnow()
    _record_event(
        session,
        persona,
        actor,
        "persona.default_changed",
        to_state=channel,
        detail={"target_user_id": target.id},
    )
    session.commit()
    session.refresh(existing)
    return target, existing


# Lifecycle events an HOB reviewer may see, and the detail keys kept for them.
# Draft edits and personal default changes stay private to the owner.
_REVIEWER_EVENT_TYPES = {
    "persona.created",
    "persona.owner_membership_ended",
    "persona.draft_submitted",
    "persona.submission_rejected",
    "persona.version_published",
    "persona.version_activated",
    "persona.version_restored",
    "persona.archived",
}
_REVIEWER_DETAIL_KEYS = {"submission_id", "revision", "content_hash"}


def events(session: Session, actor: User, persona_id: int) -> list[dict]:
    persona = _load(session, actor, persona_id)
    permissions.require_submission_access(session, actor, persona)
    rows = (
        session.query(PersonaEvent)
        .filter(PersonaEvent.persona_id == persona.id)
        .order_by(PersonaEvent.created_at.desc(), PersonaEvent.id.desc())
        .all()
    )
    owner_view = permissions.is_owner(actor, persona)
    output = []
    for row in rows:
        if not owner_view and row.event_type not in _REVIEWER_EVENT_TYPES:
            continue
        detail = dict(row.detail or {})
        if not owner_view:
            detail = {
                key: value
                for key, value in detail.items()
                if key in _REVIEWER_DETAIL_KEYS
            }
        output.append(
            {
                "id": row.id,
                "persona_id": row.persona_id,
                "persona_version_id": row.persona_version_id,
                "event_type": row.event_type,
                "actor_user_id": row.actor_user_id,
                "from_state": row.from_state,
                "to_state": row.to_state,
                "detail": detail,
                "created_at": row.created_at,
            }
        )
    return output
