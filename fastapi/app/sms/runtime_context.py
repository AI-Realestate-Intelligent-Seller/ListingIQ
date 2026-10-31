"""Trusted, immutable identity context for one AI conversation operation."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..models import Conversation, User
from ..personas.models import Persona, PersonaDefault, PersonaVersion, PersonaVersionExample


class AgentResolutionError(RuntimeError):
    """The server could not resolve one authorized speaking agent."""


@dataclass(frozen=True)
class AgentRuntimeContext:
    user_id: int
    brokerage_id: str
    conversation_id: int | None
    active_persona_id: int | None
    active_persona_version_id: int | None
    agent_name: str
    brokerage_name: str
    persona: dict | None
    examples: tuple[dict, ...] = ()


def _active_user(session: Session, user_id: int) -> User:
    user = session.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if user is None or not user.brokerage_id:
        raise AgentResolutionError("Conversation agent is missing or no longer active")
    if user.role not in {"hob", "broker", "agent"}:
        raise AgentResolutionError("Conversation owner is not authorized for Personal AI")
    return user


def resolve_user_by_trusted_email(
    session: Session, email: str, *, brokerage_id: str | None = None
) -> User:
    """Resolve an internal, already-trusted email value; never call with message text."""
    normalized = str(email or "").strip().lower()
    if not normalized:
        raise AgentResolutionError("Trusted agent email is required")
    query = session.query(User).filter(User.email == normalized, User.is_active.is_(True))
    if brokerage_id is not None:
        query = query.filter(User.brokerage_id == brokerage_id)
    rows = query.limit(2).all()
    if len(rows) != 1 or not rows[0].brokerage_id:
        raise AgentResolutionError("Trusted agent email did not resolve uniquely")
    return rows[0]


def _persona_snapshot(session: Session, user: User) -> tuple[Persona | None, PersonaVersion | None, tuple[dict, ...]]:
    default = (
        session.query(PersonaDefault)
        .filter(
            PersonaDefault.user_id == user.id,
            PersonaDefault.brokerage_id == user.brokerage_id,
            PersonaDefault.channel == "sms",
        )
        .first()
    )
    if default is None:
        return None, None, ()
    persona = session.get(Persona, default.persona_id)
    if (
        persona is None
        or persona.owner_user_id != user.id
        or persona.brokerage_id != user.brokerage_id
        or persona.membership_ended_at is not None
        or persona.status != "active"
        or persona.active_version_id is None
    ):
        return None, None, ()
    version = session.get(PersonaVersion, persona.active_version_id)
    if version is None or version.persona_id != persona.id:
        return None, None, ()
    rows = (
        session.query(PersonaVersionExample)
        .filter(
            PersonaVersionExample.version_id == version.id,
            PersonaVersionExample.channel == "sms",
            PersonaVersionExample.owner_approved == 1,
        )
        .order_by(PersonaVersionExample.position, PersonaVersionExample.id)
        .all()
    )
    examples = tuple({
        "id": row.id,
        "scenario": row.scenario,
        "language": row.language,
        "customer_message": row.customer_message,
        "preferred_response": row.preferred_response,
        "context": row.context,
        "style_explanation": row.style_explanation,
        "provenance": row.provenance,
    } for row in rows)
    return persona, version, examples


def _build_context(
    user: User,
    persona: Persona | None,
    version: PersonaVersion | None,
    examples: tuple[dict, ...],
    *,
    conversation_id: int | None,
) -> AgentRuntimeContext:
    agent_name = str((version.full_name if version else None) or user.full_name or "ListingIQ agent").strip()
    # Current account membership is trusted for brokerage identity. The Persona
    # snapshot remains lower-trust style/identity data and cannot override it.
    brokerage_name = str(user.brokerage_name or "").strip()
    snapshot = None if version is None else {
        "version_id": version.id,
        "version_number": version.version_number,
        "full_name": version.full_name,
        "display_name": version.display_name,
        "professional_role": version.professional_role,
        "professional_title": version.professional_title,
        "brokerage_name": version.brokerage_name,
        "biography": version.biography,
        "experience_summary": version.experience_summary,
        "specialties": list(version.specialties or []),
        "service_areas": list(version.service_areas or []),
        "sms_signature": version.sms_signature,
        "style_config": dict(version.style_config or {}),
        "style_instructions": version.style_instructions,
        "preferred_language": version.preferred_language,
        "profile_config": dict(version.profile_config or {}),
    }
    return AgentRuntimeContext(
        user_id=user.id,
        brokerage_id=user.brokerage_id,
        conversation_id=conversation_id,
        active_persona_id=persona.id if persona else None,
        active_persona_version_id=version.id if version else None,
        agent_name=agent_name,
        brokerage_name=brokerage_name,
        persona=snapshot,
        examples=examples,
    )


def resolve_agent_runtime_context(
    session: Session, conversation: Conversation
) -> AgentRuntimeContext:
    """Resolve identity once from trusted records for generation, tools and review."""
    if conversation.id is None or conversation.user_id is None:
        raise AgentResolutionError("Conversation has no trusted owner")
    user = _active_user(session, conversation.user_id)
    persona, version, examples = _persona_snapshot(session, user)
    return _build_context(user, persona, version, examples, conversation_id=conversation.id)


def resolve_user_runtime_context(session: Session, user: User) -> AgentRuntimeContext:
    """Trusted current-user variant used by diagnostics, never by SMS tool calls."""
    active = _active_user(session, user.id)
    persona, version, examples = _persona_snapshot(session, active)
    return _build_context(active, persona, version, examples, conversation_id=None)


def runtime_context_is_current(
    session: Session, conversation: Conversation, expected: AgentRuntimeContext
) -> bool:
    """Revalidate the frozen identity/version immediately before an AI send."""
    session.expire(conversation)
    current = resolve_agent_runtime_context(session, conversation)
    return (
        current.user_id == expected.user_id
        and current.brokerage_id == expected.brokerage_id
        and current.active_persona_version_id == expected.active_persona_version_id
    )
