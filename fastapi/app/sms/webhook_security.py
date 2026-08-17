"""Telnyx webhook signature verification.

Telnyx signs every webhook with Ed25519 over `timestamp|body`. Without this
check, anyone who learns the public URL can inject fake owner replies into a
broker's inbox and drive Bobbie's replies.

Verification is enabled by setting TELNYX_PUBLIC_KEY (Telnyx portal → Account →
Keys & Credentials → Public Key). While it is unset the endpoint accepts
unsigned posts — fine for the local simulator, unsafe once publicly reachable.
"""

import base64
import time

from ..core.config import settings
from ..logger import get_logger

logger = get_logger(__name__)

# Reject replays older than this, per Telnyx's guidance.
TOLERANCE_SECONDS = 300


class InvalidSignatureError(RuntimeError):
    """The webhook signature was missing, malformed or did not verify."""


def verification_enabled() -> bool:
    return bool(settings['telnyx'].get('public_key'))


def verify_webhook(body: bytes, signature: str | None, timestamp: str | None) -> None:
    """Raise InvalidSignatureError unless the payload is authentically Telnyx's."""
    public_key = settings['telnyx'].get('public_key')
    if not public_key:
        return  # verification not configured

    if not signature or not timestamp:
        raise InvalidSignatureError('Missing Telnyx signature headers')

    try:
        age = abs(time.time() - int(timestamp))
    except (TypeError, ValueError):
        raise InvalidSignatureError('Malformed Telnyx timestamp header')
    if age > TOLERANCE_SECONDS:
        raise InvalidSignatureError('Telnyx webhook timestamp is outside the tolerance window')

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as error:  # pragma: no cover - cryptography is a dependency
        raise InvalidSignatureError('cryptography is required to verify webhooks') from error

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
        key.verify(base64.b64decode(signature), f'{timestamp}|'.encode() + body)
    except InvalidSignature:
        raise InvalidSignatureError('Telnyx webhook signature did not verify')
    except Exception as error:
        raise InvalidSignatureError(f'Could not verify Telnyx webhook: {error}') from error
