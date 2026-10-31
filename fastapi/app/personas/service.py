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
    PersonaDraftExample,
    PersonaEvent,
    PersonaSubmission,
    PersonaSubmissionExample,
    PersonaVersion,
    PersonaVersionExample,
)
from .schemas import (
    CommunicationStyle,
    PersonaCreate,
    PersonaDraftUpdate,
    PersonaExampleCreate,
    PersonaExampleUpdate,
    PersonaProfileConfig,
    SourceReview,
)


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
        profile_config=PersonaProfileConfig().dict(),
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


# Profile maps the owner always edits as a whole (a removed N/A reason must disappear).
_REPLACED_PROFILE_KEYS = frozenset({"confirmations", "na_reasons"})


def _merge_config(current: dict, incoming, field: str) -> dict:
    """Apply the supplied keys of a style/profile object onto the stored one."""
    supplied = incoming.dict(exclude_unset=True)

    def merge(base: dict, patch: dict, top: bool) -> dict:
        result = dict(base)
        for key, value in patch.items():
            if isinstance(value, dict) and isinstance(result.get(key), dict) and not (top and key in _REPLACED_PROFILE_KEYS):
                result[key] = merge(result[key], value, False)
            else:
                result[key] = value
        return result

    model = PersonaProfileConfig if field == "profile_config" else CommunicationStyle
    try:
        base = model.parse_obj(current or {}).dict()
    except ValidationError:
        # A stored value that no longer validates is replaced by the new one.
        base = model().dict()
    return model.parse_obj(merge(base, supplied, True)).dict()


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
        if field in {"style_config", "profile_config"}:
            # Only the keys the client sent change; every other stored value
            # (including fields the simplified form no longer shows) is kept.
            value = _merge_config(getattr(draft, field) or {}, value, field)
        if field == "profile_config":
            value = _owner_review_update(draft.profile_config or {}, value)
        if getattr(draft, field) != value:
            values[field] = value
            changed_fields.append(field)
    # Saving the form approves the owner's examples in the same transaction.
    unapproved = session.query(PersonaDraftExample.id).filter(
        PersonaDraftExample.persona_id == persona.id, PersonaDraftExample.owner_approved == 0,
    ).count()
    if not changed_fields and not unapproved:
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
    if unapproved:
        session.execute(
            update(PersonaDraftExample)
            .where(PersonaDraftExample.persona_id == persona.id, PersonaDraftExample.owner_approved == 0)
            .values(owner_approved=1)
            .execution_options(synchronize_session=False)
        )
    session.expire(draft)
    session.refresh(draft)
    _record_event(
        session,
        persona,
        actor,
        "persona.draft_changed",
        detail={"revision": next_revision, "fields": sorted(changed_fields), "examples_approved": unapproved},
    )
    session.commit()
    session.refresh(draft)
    return draft


def _advance_draft_revision(
    session: Session, persona: Persona, actor: User, expected_revision: int, event_type: str
) -> PersonaDraft:
    result = session.execute(
        update(PersonaDraft)
        .where(
            PersonaDraft.persona_id == persona.id,
            PersonaDraft.revision == expected_revision,
        )
        .values(
            revision=expected_revision + 1,
            review_status="draft",
            submitted_by_user_id=None,
            submitted_at=None,
            last_editor_user_id=actor.id,
            updated_at=datetime.utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        session.rollback()
        raise PersonaConflict("Draft revision is stale; reload the draft and retry")
    _record_event(
        session,
        persona,
        actor,
        event_type,
        detail={"revision": expected_revision + 1},
    )
    return persona.draft


def list_draft_examples(
    session: Session, actor: User, persona_id: int, page: int, page_size: int,
    channel: str | None = None, scenario: str | None = None,
):
    persona = _load(session, actor, persona_id, owner=True)
    query = session.query(PersonaDraftExample).filter(PersonaDraftExample.persona_id == persona.id)
    if channel:
        query = query.filter(PersonaDraftExample.channel == channel)
    if scenario:
        query = query.filter(PersonaDraftExample.scenario.ilike(f"%{scenario}%"))
    total = query.count()
    items = query.order_by(PersonaDraftExample.position, PersonaDraftExample.id).offset((page - 1) * page_size).limit(page_size).all()
    return items, total, persona.draft.revision


def create_example(
    session: Session, actor: User, persona_id: int, payload: PersonaExampleCreate
) -> PersonaDraftExample:
    persona = _load(session, actor, persona_id, owner=True)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be edited")
    if persona.draft.revision != payload.revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {persona.draft.revision}")
    maximum = session.query(func.max(PersonaDraftExample.position)).filter(PersonaDraftExample.persona_id == persona.id).scalar()
    position = (maximum if maximum is not None else -1) + 1
    values = payload.dict(exclude={"revision"})
    # The owner saving their own example is the approval; no separate step.
    values["owner_approved"] = 1
    row = PersonaDraftExample(persona_id=persona.id, position=position, **values)
    session.add(row)
    _advance_draft_revision(session, persona, actor, payload.revision, "persona.example_created")
    session.commit()
    session.refresh(row)
    return row


def update_example(
    session: Session, actor: User, persona_id: int, example_id: int, payload: PersonaExampleUpdate
) -> PersonaDraftExample:
    persona = _load(session, actor, persona_id, owner=True)
    if persona.draft.revision != payload.revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {persona.draft.revision}")
    row = session.query(PersonaDraftExample).filter(PersonaDraftExample.id == example_id, PersonaDraftExample.persona_id == persona.id).first()
    if row is None:
        raise _not_found("Conversation example not found")
    changes = {field: getattr(payload, field) for field in payload.__fields_set__ if field != "revision"}
    if not changes:
        raise PersonaValidationFailure("No example changes were supplied", ["no_changes"])
    # Whatever the owner saves is their approved wording.
    changes["owner_approved"] = True
    changed = False
    for field, value in changes.items():
        if field == "owner_approved":
            value = int(value)
        if getattr(row, field) != value:
            setattr(row, field, value)
            changed = True
    if not changed:
        return row
    _advance_draft_revision(session, persona, actor, payload.revision, "persona.example_changed")
    session.commit()
    session.refresh(row)
    return row


def save_examples(session: Session, actor: User, persona_id: int, payload) -> list[PersonaDraftExample]:
    """Save the owner's whole example list in one transaction.

    Listed examples are created or updated in the given order, examples the
    owner removed are deleted, and every saved example is owner-approved
    because the owner saved it. Import metadata (context, flags, provenance)
    of existing examples is kept. One revision check, one revision bump.
    """
    persona = _load(session, actor, persona_id, owner=True)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be edited")
    if persona.draft.revision != payload.revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {persona.draft.revision}")
    rows = {row.id: row for row in session.query(PersonaDraftExample).filter(PersonaDraftExample.persona_id == persona.id)}
    unknown = [item.id for item in payload.examples if item.id is not None and item.id not in rows]
    if unknown:
        raise _not_found("Conversation example not found")
    kept_ids, seen, ordered = set(), set(), []
    for item in payload.examples:
        key = (item.scenario, item.channel, item.customer_message, item.preferred_response)
        if key in seen or (item.id is not None and item.id in kept_ids):
            continue  # an exact repeat in the same save is stored once
        seen.add(key)
        values = {field: getattr(item, field) for field in ("scenario", "channel", "language", "customer_message", "preferred_response")}
        if item.id is not None:
            row = rows[item.id]
            kept_ids.add(item.id)
            if item.provenance is not None:
                values["provenance"] = item.provenance
            for field, value in values.items():
                setattr(row, field, value)
            row.owner_approved = 1
        else:
            row = PersonaDraftExample(
                persona_id=persona.id, position=0, context="", style_explanation="", review_flags=[],
                provenance=item.provenance or "user_written", owner_approved=1, **values,
            )
            session.add(row)
        ordered.append(row)
    for row_id, row in rows.items():
        if row_id not in kept_ids:
            session.delete(row)
    session.flush()
    for position, row in enumerate(ordered):
        row.position = position
    _advance_draft_revision(session, persona, actor, payload.revision, "persona.examples_saved")
    session.commit()
    for row in ordered:
        session.refresh(row)
    return ordered


def delete_example(
    session: Session, actor: User, persona_id: int, example_id: int, revision: int
) -> int:
    persona = _load(session, actor, persona_id, owner=True)
    if persona.draft.revision != revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {persona.draft.revision}")
    row = session.query(PersonaDraftExample).filter(PersonaDraftExample.id == example_id, PersonaDraftExample.persona_id == persona.id).first()
    if row is None:
        raise _not_found("Conversation example not found")
    session.delete(row)
    _advance_draft_revision(session, persona, actor, revision, "persona.example_removed")
    session.commit()
    return revision + 1


def duplicate_example(
    session: Session, actor: User, persona_id: int, example_id: int, revision: int
) -> PersonaDraftExample:
    persona = _load(session, actor, persona_id, owner=True)
    if persona.draft.revision != revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {persona.draft.revision}")
    source = session.query(PersonaDraftExample).filter(PersonaDraftExample.id == example_id, PersonaDraftExample.persona_id == persona.id).first()
    if source is None:
        raise _not_found("Conversation example not found")
    values = _example_snapshot(source)
    values["scenario"] = f"{values['scenario']} (copy)"[:120]
    values["owner_approved"] = 1
    maximum = session.query(func.max(PersonaDraftExample.position)).filter(PersonaDraftExample.persona_id == persona.id).scalar()
    position = (maximum if maximum is not None else -1) + 1
    row = PersonaDraftExample(persona_id=persona.id, position=position, **values)
    session.add(row)
    _advance_draft_revision(session, persona, actor, revision, "persona.example_duplicated")
    session.commit()
    session.refresh(row)
    return row


def list_submission_examples(session: Session, actor: User, persona_id: int, submission_id: int, page: int, page_size: int):
    submission(session, actor, persona_id, submission_id)
    query = session.query(PersonaSubmissionExample).filter(PersonaSubmissionExample.submission_id == submission_id)
    return query.order_by(PersonaSubmissionExample.position).offset((page - 1) * page_size).limit(page_size).all(), query.count()


def list_version_examples(session: Session, actor: User, persona_id: int, version_number: int, page: int, page_size: int):
    selected = version(session, actor, persona_id, version_number)
    query = session.query(PersonaVersionExample).filter(PersonaVersionExample.version_id == selected.id)
    return query.order_by(PersonaVersionExample.position).offset((page - 1) * page_size).limit(page_size).all(), query.count()


# What the owner must supply before submitting. These are exactly the fields
# the simplified setup form shows; other stored profile fields stay optional
# and are kept as they are. Communication style is captured through
# owner-approved conversation examples, not through descriptive settings.
REQUIRED_PROFILE_FIELDS = {
    "who_i_am": ("full_name", "professional_title", "license_number", "licensing_jurisdiction", "licensing_status"),
    "professional_background": ("biography", "specialties", "service_areas"),
}
# Fields that may instead be marked not applicable with a written reason.
NOT_APPLICABLE_ALLOWED = frozenset({"professional_title", "license_number", "licensing_jurisdiction", "licensing_status"})
FIELD_LABELS = {
    "full_name": "Full name", "professional_title": "Professional title", "license_number": "License number",
    "licensing_jurisdiction": "Licensing jurisdiction", "licensing_status": "License status", "biography": "Biography",
    "specialties": "Specialties", "service_areas": "Service areas", "brokerage_name": "Brokerage",
    "preferred_language": "Primary language",
}


def _persona_size_check(draft, examples) -> tuple[str | None, str | None]:
    """Refuse a Persona whose live AI context would exceed the runtime budget.

    Every approved SMS example is sent with every AI request, so the check
    measures exactly what the runtime builds, with the same estimate.
    """
    from ..sms.prompt import (PERSONA_PUBLISH_TOKEN_LIMIT, PERSONA_PUBLISH_WARNING_TOKENS,
                              persona_context_tokens)

    persona = {field: getattr(draft, field, None) for field in (
        "professional_title", "professional_role", "biography", "experience_summary", "specialties",
        "service_areas", "sms_signature", "style_config", "style_instructions", "preferred_language",
        "profile_config")}
    approved = [{field: getattr(row, field, "") or "" for field in (
        "scenario", "language", "customer_message", "preferred_response", "context", "style_explanation")}
        for row in examples or []
        if getattr(row, "channel", None) == "sms" and bool(getattr(row, "owner_approved", 0))]
    tokens = persona_context_tokens(persona, approved, str(getattr(draft, "full_name", "") or ""),
                                    str(getattr(draft, "brokerage_name", "") or ""))
    if tokens > PERSONA_PUBLISH_TOKEN_LIMIT:
        return (f"conversation_examples.too_large: the approved SMS examples, style and playbook come to about "
                f"{tokens:,} tokens; the limit is {PERSONA_PUBLISH_TOKEN_LIMIT:,}. Shorten or remove some "
                f"examples so your AI can keep replying."), None
    if tokens > PERSONA_PUBLISH_WARNING_TOKENS:
        return None, (f"approved SMS examples, style and playbook are large (about {tokens:,} tokens of the "
                      f"{PERSONA_PUBLISH_TOKEN_LIMIT:,} limit); every AI request carries all of them")
    return None, None


def validation_result(draft: PersonaDraft, examples: list[PersonaDraftExample] | None = None) -> dict:
    """The one completeness check used by import review and by submission."""
    errors = []
    # Account-level values; the setup form shows them, so they must be present.
    for field in ("brokerage_name", "preferred_language"):
        if not str(getattr(draft, field, "") or "").strip():
            errors.append(f"{field} is required")
    try:
        CommunicationStyle.parse_obj(draft.style_config or {})
    except ValidationError:
        errors.append("communication style configuration is invalid")
    warnings = []
    sections = {
        "who_i_am": {"complete": False, "missing": []},
        "professional_background": {"complete": False, "missing": []},
        "conversation_examples": {"complete": bool(examples), "missing": []},
    }
    try:
        profile = PersonaProfileConfig.parse_obj(draft.profile_config or {})
    except ValidationError:
        profile = None
        errors.append("personal AI profile configuration is invalid")
        sections["who_i_am"] = {"complete": False, "missing": ["invalid configuration"]}
    if profile:
        values = {
            "full_name": draft.full_name,
            "professional_title": draft.professional_title,
            "license_number": profile.identity.license_number,
            "licensing_jurisdiction": profile.identity.licensing_jurisdiction,
            "licensing_status": profile.identity.licensing_status,
            "biography": draft.biography,
            "specialties": draft.specialties,
            "service_areas": draft.service_areas,
        }
        for section, fields in REQUIRED_PROFILE_FIELDS.items():
            missing = []
            for field in fields:
                value = values[field]
                supplied = bool(value) if not isinstance(value, str) else bool(value.strip())
                not_applicable = (
                    field in NOT_APPLICABLE_ALLOWED
                    and profile.confirmations.get(field) == "not_applicable"
                    and profile.na_reasons.get(field, "").strip()
                )
                if not supplied and not not_applicable:
                    missing.append(field)
            sections[section] = {"complete": not missing, "missing": missing}
            errors.extend(
                f"{section}.{field} is required" + (" or needs a reasoned N/A" if field in NOT_APPLICABLE_ALLOWED else "")
                for field in missing
            )
        examples = examples or []
        missing_examples = []
        if not examples:
            missing_examples.append("at least one conversation example")
        if examples and not any(bool(row.owner_approved) for row in examples):
            missing_examples.append("at least one owner-approved example")
        sections["conversation_examples"] = {"complete": not missing_examples, "missing": missing_examples}
        errors.extend(f"conversation_examples.{item} is required" for item in missing_examples)
        channels = {row.channel for row in examples}
        for channel in ("sms", "email"):
            if channel not in channels:
                warnings.append(f"no {channel.upper()} conversation example supplied")
        flagged = sum(1 for row in examples if getattr(row, "review_flags", None))
        if flagged:
            warnings.append(f"{flagged} conversation example(s) carry import review flags")
        size_error, size_warning = _persona_size_check(draft, examples)
        if size_error:
            errors.append(size_error)
            sections["conversation_examples"]["complete"] = False
            sections["conversation_examples"]["missing"].append("a shorter set of SMS examples")
        if size_warning:
            warnings.append(size_warning)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "revision": draft.revision,
        "sections": sections,
        "missing_count": len(errors),
    }


def validate(session: Session, actor: User, persona_id: int) -> dict:
    draft = get_draft(session, actor, persona_id)
    examples = session.query(PersonaDraftExample).filter(PersonaDraftExample.persona_id == persona_id).all()
    return validation_result(draft, examples)


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
    draft_examples = (
        session.query(PersonaDraftExample)
        .filter(PersonaDraftExample.persona_id == persona.id)
        .order_by(PersonaDraftExample.position, PersonaDraftExample.id)
        .all()
    )
    result = validation_result(draft, draft_examples)
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
    example_snapshots = [_example_snapshot(row) for row in draft_examples]
    submission = PersonaSubmission(
        persona_id=persona.id,
        submission_number=submission_number,
        draft_revision=revision,
        snapshot=snapshot,
        content_hash=_content_hash(snapshot, example_snapshots),
        status="submitted",
        submitted_by_user_id=actor.id,
        submitted_at=datetime.utcnow(),
    )
    session.add(submission)
    try:
        # A concurrent submit may take the same submission number or the
        # single pending slot first; the database constraint decides.
        session.flush()
    except IntegrityError as error:
        session.rollback()
        raise PersonaConflict(
            "This Persona already has a submission awaiting review"
        ) from error
    for position, (draft_example, example) in enumerate(zip(draft_examples, example_snapshots)):
        session.add(PersonaSubmissionExample(
            submission_id=submission.id,
            source_example_id=draft_example.id,
            position=position,
            **example,
        ))
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
    "profile_config",
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


_EXAMPLE_FIELDS = (
    "scenario", "channel", "language", "customer_message", "preferred_response",
    "context", "style_explanation", "provenance", "owner_approved",
)


def _example_snapshot(example) -> dict:
    result = {field: getattr(example, field) for field in _EXAMPLE_FIELDS}
    result["owner_approved"] = int(bool(result["owner_approved"]))
    # Flags join the snapshot (and its content hash) only when present, so
    # submissions frozen before flags existed keep their original hash.
    flags = list(getattr(example, "review_flags", None) or [])
    if flags:
        result["review_flags"] = flags
    return result


def _owner_review_update(current: dict, incoming: dict) -> dict:
    """Keep server-written import review records; accept only owner decisions.

    The owner may confirm, dispute, keep or dismiss imported items and add a
    note, but cannot delete, add or relabel what the source document said.
    """
    existing = (current or {}).get("source_review") or {}
    proposed = (incoming or {}).get("source_review") or {}

    def decisions(items, key, fields):
        return {key(item): {name: item[name] for name in fields if name in item} for item in items or []}

    claim_key = lambda item: (item.get("statement"), item.get("source_status"), item.get("field_path"))  # noqa: E731
    detail_key = lambda item: (item.get("section"), item.get("detail"))  # noqa: E731
    claim_decisions = decisions(proposed.get("claims"), claim_key, ("owner_status", "owner_note"))
    detail_decisions = decisions(proposed.get("unmapped_details"), detail_key, ("owner_status",))
    review = {key: value for key, value in existing.items()}
    review["claims"] = [{**item, **claim_decisions.get(claim_key(item), {})} for item in existing.get("claims") or []]
    review["unmapped_details"] = [
        {**item, **detail_decisions.get(detail_key(item), {})} for item in existing.get("unmapped_details") or []
    ]
    result = dict(incoming)
    result["source_review"] = SourceReview.parse_obj(review).dict()
    return result


def _content_hash(snapshot: dict, examples: list[dict] | None = None) -> str:
    content = snapshot if not examples else {"profile": snapshot, "examples": examples}
    encoded = json.dumps(
        content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
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
    submission_examples = sorted(submission.examples, key=lambda row: (row.position, row.id))
    example_snapshots = [_example_snapshot(row) for row in submission_examples]
    if (
        set(snapshot) != set(_SNAPSHOT_FIELDS)
        or _content_hash(snapshot, example_snapshots) != content_hash
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
    for position, (source, example) in enumerate(zip(submission_examples, example_snapshots)):
        session.add(PersonaVersionExample(
            version_id=version.id,
            source_submission_example_id=source.id,
            position=position,
            **example,
        ))
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
    # Outbox row only; no vector-store call happens inside this transaction.
    from .knowledge_indexing import queue_version
    queue_version(session, persona, version, actor.id)
    session.commit()
    session.refresh(version)
    return version


def _next_version_number(session: Session, persona_id: int) -> int:
    return (
        session.query(func.max(PersonaVersion.version_number))
        .filter(PersonaVersion.persona_id == persona_id)
        .scalar()
        or 0
    ) + 1


def _ensure_owner_sms_default(session: Session, actor: User, persona: Persona) -> PersonaDefault:
    """Make this owner's active Personal Persona their SMS default in the caller's transaction."""
    permissions.require_owner(actor, persona)
    row = (
        session.query(PersonaDefault)
        .filter(PersonaDefault.user_id == actor.id, PersonaDefault.channel == "sms")
        .first()
    )
    now = datetime.utcnow()
    if row is None:
        row = PersonaDefault(
            brokerage_id=actor.brokerage_id,
            user_id=actor.id,
            channel="sms",
            persona_id=persona.id,
            updated_by_user_id=actor.id,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
    else:
        row.brokerage_id = actor.brokerage_id
        row.persona_id = persona.id
        row.updated_by_user_id = actor.id
        row.updated_at = now
    return row


def publish_own(session: Session, actor: User, persona_id: int, revision: int) -> tuple[PersonaVersion, bool]:
    """The owner publishes their own Personal Persona directly; no HOB review.

    Publishes exactly the saved draft revision the owner reviewed (409 if it
    changed), only when it passes the submission completeness rules. The new
    immutable version, with its examples and their provenance and flags, is
    activated for the owner. Publishing a configuration that is already
    published creates nothing new and returns that version. Existing
    submissions, including pending ones, are left untouched.

    Returns the version and whether it was created now.
    """
    persona = _load(session, actor, persona_id, owner=True, lock=True)
    if persona.status == "archived":
        raise PersonaConflict("An archived Persona cannot be published")
    draft = persona.draft
    if draft.revision != revision:
        raise PersonaConflict(f"Draft revision is stale; current revision is {draft.revision}")
    draft_examples = (
        session.query(PersonaDraftExample)
        .filter(PersonaDraftExample.persona_id == persona.id)
        .order_by(PersonaDraftExample.position, PersonaDraftExample.id)
        .all()
    )
    result = validation_result(draft, draft_examples)
    if not result["valid"]:
        raise PersonaValidationFailure("Persona draft is not complete", result["errors"])
    snapshot = _snapshot(draft)
    example_snapshots = [_example_snapshot(row) for row in draft_examples]
    content_hash = _content_hash(snapshot, example_snapshots)

    def already_published():
        return session.query(PersonaVersion).filter(
            PersonaVersion.persona_id == persona.id, PersonaVersion.content_hash == content_hash,
        ).first()

    existing = already_published()
    if existing is None:
        version = PersonaVersion(
            persona_id=persona.id,
            version_number=_next_version_number(session, persona.id),
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
            # A concurrent click published first; the database constraints decide.
            session.rollback()
            existing = already_published()
            if existing is None:
                raise PersonaConflict("Persona publication conflicted with another publication; reload and retry") from error
            return existing, False
        for position, (source, example) in enumerate(zip(draft_examples, example_snapshots)):
            session.add(PersonaVersionExample(
                version_id=version.id, source_submission_example_id=None, position=position, **example,
            ))
        created = True
    else:
        version, created = existing, False
    previous = persona.active_version
    _mark_draft_if_revision(session, persona.id, revision, base_version_id=version.id, review_status="published")
    if created:
        _record_event(
            session, persona, actor, "persona.version_published", version=version,
            to_state=f"v{version.version_number}",
            detail={"revision": revision, "content_hash": content_hash, "self_published": True},
        )
    if previous is None or previous.id != version.id:
        persona.active_version_id = version.id
        persona.display_name = version.display_name
        persona.updated_at = datetime.utcnow()
        _record_event(
            session, persona, actor, "persona.version_activated", version=version,
            from_state=f"v{previous.version_number}" if previous else None, to_state=f"v{version.version_number}",
        )
    _ensure_owner_sms_default(session, actor, persona)
    from .knowledge_indexing import queue_version
    queue_version(session, persona, version, actor.id)
    session.commit()
    session.refresh(version)
    return version, created


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
    permissions.require_activate(session, actor, persona)
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
    from .knowledge_indexing import queue_version
    if previous and previous.id == selected.id:
        if permissions.is_owner(actor, persona):
            _ensure_owner_sms_default(session, actor, persona)
        queue_version(session, persona, selected, actor.id)
        session.commit()
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
    if permissions.is_owner(actor, persona):
        _ensure_owner_sms_default(session, actor, persona)
    # Activation by the owner or a reviewer: the selected version's knowledge
    # must be indexed for the persona owner either way (idempotent per version).
    queue_version(session, persona, selected, actor.id)
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
