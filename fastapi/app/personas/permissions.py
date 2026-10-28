"""Authorization rules for Persona operations.

Rules (all derived from the authenticated user, never from client input):

* A Persona belongs to exactly one user in exactly one brokerage.
* Only the owner, while still a member of that brokerage, may read or change
  the working draft.
* A Head of Brokerage (HOB) of the same brokerage may see a Persona only once
  it has submitted or published material, and then only that material: the
  immutable submission snapshots and the published versions. An HOB never
  sees or edits the working draft.
* Brokers and agents never see another person's Persona.
* Membership is checked against the user's current ``brokerage_id`` and the
  Persona's ``membership_ended_at``. A database trigger stamps the latter
  whenever the owner's brokerage changes, so a user who moves brokerage loses
  the old Persona for good: rejoining later is a fresh membership and needs a
  fresh Persona. Its published history stays readable for HOB audit only.
"""

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import Session

from ..models import User
from .models import Persona, PersonaSubmission

PERSONA_ROLES = {"hob", "broker", "agent"}


class PersonaAccessError(RuntimeError):
    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found


def require_member(user: User) -> None:
    if user.role not in PERSONA_ROLES or not user.is_active:
        raise PersonaAccessError("Your role does not have access to AI Personas")
    if not user.brokerage_id:
        raise PersonaAccessError("Your brokerage information is incomplete")


def same_brokerage(user: User, persona: Persona) -> bool:
    return bool(user.brokerage_id and user.brokerage_id == persona.brokerage_id)


def is_owner(user: User, persona: Persona) -> bool:
    """The current owner: same user, same brokerage, same unbroken membership."""
    return (
        same_brokerage(user, persona)
        and persona.owner_user_id == user.id
        and persona.membership_ended_at is None
    )


def has_review_material(persona: Persona) -> bool:
    return persona.active_version_id is not None or bool(persona.submissions)


def is_reviewer_viewer(user: User, persona: Persona) -> bool:
    return (
        user.role == "hob"
        and same_brokerage(user, persona)
        and not is_owner(user, persona)
        and has_review_material(persona)
    )


def can_view(user: User, persona: Persona) -> bool:
    return is_owner(user, persona) or is_reviewer_viewer(user, persona)


def visible_filter(user: User):
    """SQL equivalent of ``can_view`` for list queries."""
    own = and_(Persona.owner_user_id == user.id, Persona.membership_ended_at.is_(None))
    if user.role != "hob":
        return Persona.brokerage_id == user.brokerage_id, own
    reviewable = or_(
        Persona.active_version_id.isnot(None),
        exists().where(PersonaSubmission.persona_id == Persona.id),
    )
    return Persona.brokerage_id == user.brokerage_id, or_(own, reviewable)


def require_view(user: User, persona: Persona) -> None:
    require_member(user)
    if not can_view(user, persona):
        # Cross-tenant and other people's Personas are not disclosed.
        raise PersonaAccessError("Persona not found", not_found=True)


def require_owner(user: User, persona: Persona) -> None:
    require_member(user)
    if not is_owner(user, persona):
        raise PersonaAccessError("Persona draft not found", not_found=True)


# Backwards-compatible name used by earlier foundation code.
require_edit = require_owner


def require_reviewer(session: Session, user: User, persona: Persona) -> None:
    require_member(user)
    if not same_brokerage(user, persona) or not (
        is_owner(user, persona) or has_review_material(persona)
    ):
        raise PersonaAccessError("Persona not found", not_found=True)
    if user.role != "hob":
        raise PersonaAccessError(
            "Only a Head of Brokerage can review or publish Persona submissions"
        )
    if persona.membership_ended_at is not None:
        raise PersonaAccessError(
            "Persona owner's membership has ended; this Persona is retained for audit only"
        )
    owner = session.get(User, persona.owner_user_id)
    if (
        owner is None
        or not owner.is_active
        or owner.brokerage_id != persona.brokerage_id
    ):
        raise PersonaAccessError(
            "Persona owner is no longer an active member of this brokerage"
        )


require_publish = require_reviewer


def require_submission_access(session: Session, user: User, persona: Persona) -> None:
    """Owner or an HOB of the brokerage may read submissions and history.

    This is read-only audit access, so it also covers Personas whose owner has
    since left; decisions go through ``require_reviewer``.
    """
    require_member(user)
    if is_owner(user, persona) or is_reviewer_viewer(user, persona):
        return
    if same_brokerage(user, persona) and has_review_material(persona):
        raise PersonaAccessError("Only a Head of Brokerage can review Persona history")
    raise PersonaAccessError("Persona not found", not_found=True)


def can_use(target: User, persona: Persona) -> bool:
    """A user may speak only through their own Persona in their current brokerage."""
    return is_owner(target, persona)


def resolve_default_target(
    session: Session, actor: User, target_user_id: int | None, *, write: bool
) -> User:
    """Defaults are the user's own setting.

    Only the user may change their default. An HOB may read the resolved
    (published) default of an active member of their own brokerage.
    """
    require_member(actor)
    requested_id = target_user_id or actor.id
    if requested_id == actor.id:
        return actor
    if write or actor.role != "hob":
        raise PersonaAccessError("You can manage only your own default Persona")
    target = session.get(User, requested_id)
    if (
        target is None
        or not target.is_active
        or not target.brokerage_id
        or target.brokerage_id != actor.brokerage_id
        or target.role not in PERSONA_ROLES
    ):
        raise PersonaAccessError(
            "Default user is not an active member of your brokerage", not_found=True
        )
    return target
