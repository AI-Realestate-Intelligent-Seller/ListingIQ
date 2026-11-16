"""Which Lofty credential a ListingIQ user may act with.

Today there is one shared API key for the SpaceMatch Lofty account. A brokerage
may use it only when a platform admin has turned on the brokerage-scoped
feature flag ``lofty_export`` for it. A global (unscoped) flag is ignored on
purpose: one account's key must never open up to every tenant. Lead, mute and note logic
only ever sees a ``LoftyCredentials`` object, so agent-level OAuth can later
return a different implementation from ``resolve_credentials`` without
touching that logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import FeatureFlag, User

FEATURE_FLAG = 'lofty_export'


class LoftyNotAvailable(RuntimeError):
    """The user cannot use Lofty: not configured, or not their organization."""

    def __init__(self, message: str, *, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


class LoftyCredentials(Protocol):
    @property
    def reference(self) -> str:
        """A non-secret label stored with each export, e.g. ``api_key:spacematch``."""

    def authorization_header(self) -> str:
        """The value for the ``Authorization`` header."""


@dataclass(frozen=True)
class ApiKeyCredentials:
    api_key: str
    label: str

    @property
    def reference(self) -> str:
        return f'api_key:{self.label}'

    def authorization_header(self) -> str:
        return f'token {self.api_key}'

    def __repr__(self) -> str:  # never print the key
        return f'ApiKeyCredentials(label={self.label!r})'


def brokerage_enabled(session: Session, brokerage_id: str | None) -> bool:
    if not brokerage_id:
        return False
    return session.query(FeatureFlag.id).filter(
        FeatureFlag.key == FEATURE_FLAG,
        FeatureFlag.brokerage_id == brokerage_id,
        FeatureFlag.enabled.is_(True),
    ).first() is not None


def resolve_credentials(session: Session, user: User) -> LoftyCredentials:
    """Return the credential this user may export with, or raise LoftyNotAvailable."""
    config = settings['lofty']
    if not config['api_key']:
        raise LoftyNotAvailable('Lofty is not configured on this server.', status_code=503)
    if not brokerage_enabled(session, user.brokerage_id):
        raise LoftyNotAvailable(
            'Lofty is not enabled for your organization. A platform admin can turn it on in '
            'Platform admin → Feature flags (key "lofty_export", with your brokerage ID).')
    return ApiKeyCredentials(api_key=config['api_key'], label=config['connection_label'])


def is_available(session: Session, user: User) -> bool:
    try:
        resolve_credentials(session, user)
    except LoftyNotAvailable:
        return False
    return True
