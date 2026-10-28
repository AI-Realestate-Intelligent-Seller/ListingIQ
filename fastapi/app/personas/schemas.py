"""Validated HTTP contracts for the Persona foundation.

Request models are strict: unknown fields are rejected, explicit JSON null is
rejected for every non-nullable field, numbers are never coerced from strings,
booleans or floats, and every text field has a length bound that matches its
database column. Invalid payloads therefore fail with HTTP 422 before any
database work starts.
"""

import re
import unicodedata
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, StrictStr, conint, root_validator, validator

# Largest value a PostgreSQL INTEGER column can hold. Identifiers outside this
# range are rejected up front instead of surfacing as a database error.
MAX_DB_INT = 2_147_483_647

# Strict integers: JSON strings, floats and booleans are rejected, not coerced.
DbId = conint(strict=True, ge=1, le=MAX_DB_INT)
StyleLevel = conint(strict=True, ge=1, le=5)

_POLICY_OVERRIDE = re.compile(
    r"(?:ignore|override|bypass|disable)\s+(?:the\s+)?(?:system|platform|policy|policies|safety|stop|dnc)"
    r"|(?:pretend|claim)\s+(?:to\s+be\s+)?(?:a\s+)?human"
    r"|do\s+not\s+disclos(?:e|ure)\s+(?:ai|automation)",
    re.IGNORECASE,
)


def _reject_nulls(values, nullable: frozenset = frozenset()):
    """Explicit null differs from omission: only listed fields may be null."""
    if isinstance(values, dict):
        for field, value in values.items():
            if value is None and field not in nullable:
                raise ValueError(f"{field} cannot be null")
    return values


def _clean_text(value):
    """Trim strings; leave other types for the Strict* validators to reject."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


def _reject_control_characters(value):
    if isinstance(value, str) and any(
        unicodedata.category(character) == "Cc" and character not in "\n\t"
        for character in value
    ):
        raise ValueError("Control characters are not allowed")
    return value


def _single_line(value):
    if isinstance(value, str) and any(
        unicodedata.category(character) in {"Cc", "Zl", "Zp"} for character in value
    ):
        raise ValueError("Must be a single line without control characters")
    return value


def _clean_list(values, *, max_items: int, max_length: int):
    if len(values) > max_items:
        raise ValueError(f"At most {max_items} values are allowed")
    cleaned = []
    for value in values:
        item = _clean_text(value)
        _single_line(item)
        if not item:
            continue
        if len(item) > max_length:
            raise ValueError(f"Each value must be at most {max_length} characters")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


class StrictModel(BaseModel):
    class Config:
        extra = "forbid"
        anystr_strip_whitespace = False


class CommunicationStyle(StrictModel):
    """Structured style settings stored per Persona version.

    The object is replaced as a whole on update; omitted keys take the
    documented defaults below.
    """

    personality: StrictStr = Field(
        "professional and approachable", min_length=1, max_length=300
    )
    tone: Literal[
        "professional",
        "conversational",
        "warm",
        "direct",
        "calm",
        "energetic",
        "custom",
    ] = "conversational"
    formality: StyleLevel = 3
    warmth: StyleLevel = 3
    directness: StyleLevel = 3
    message_length: Literal["brief", "balanced", "detailed"] = "brief"
    sentence_pattern: Literal["short", "mixed", "long"] = "short"
    greeting_preference: StrictStr = Field("", max_length=160)
    vocabulary_preference: StrictStr = Field("plain language", max_length=500)
    question_behavior: StrictStr = Field("", max_length=500)
    follow_up_behavior: StrictStr = Field("", max_length=500)
    objection_handling: StrictStr = Field("", max_length=1000)
    sign_off_preference: StrictStr = Field("", max_length=160)
    preferred_phrases: list[StrictStr] = Field(default_factory=list)
    avoided_phrases: list[StrictStr] = Field(default_factory=list)

    _no_nulls = root_validator(pre=True, allow_reuse=True)(
        lambda cls, values: _reject_nulls(values)
    )

    @validator(
        "personality",
        "greeting_preference",
        "vocabulary_preference",
        "question_behavior",
        "follow_up_behavior",
        "objection_handling",
        "sign_off_preference",
        pre=True,
    )
    def _strip_text(cls, value):
        return _reject_control_characters(_clean_text(value))

    @validator("preferred_phrases", "avoided_phrases")
    def _bounded_phrases(cls, values):
        return _clean_list(values, max_items=20, max_length=80)


class PersonaCreate(StrictModel):
    """Create the caller's own personal Persona.

    Ownership and brokerage always come from the authenticated user, so the
    contract has no owner, brokerage or scope fields; sending one is a 422.
    """

    full_name: StrictStr = Field(min_length=1, max_length=255)
    display_name: Optional[StrictStr] = Field(None, min_length=1, max_length=120)
    slug: Optional[StrictStr] = Field(
        None, min_length=2, max_length=100, regex=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )

    _no_nulls = root_validator(pre=True, allow_reuse=True)(
        lambda cls, values: _reject_nulls(values)
    )

    @validator("full_name", "display_name", pre=True)
    def _strip_identity(cls, value):
        return _single_line(_clean_text(value))


class PersonaDraftUpdate(StrictModel):
    """Partial draft update guarded by the expected draft revision."""

    revision: DbId
    full_name: Optional[StrictStr] = Field(None, min_length=1, max_length=255)
    display_name: Optional[StrictStr] = Field(None, min_length=1, max_length=120)
    professional_role: Optional[StrictStr] = Field(None, max_length=120)
    professional_title: Optional[StrictStr] = Field(None, max_length=160)
    brokerage_name: Optional[StrictStr] = Field(None, max_length=255)
    biography: Optional[StrictStr] = Field(None, max_length=5000)
    experience_summary: Optional[StrictStr] = Field(None, max_length=5000)
    specialties: Optional[list[StrictStr]] = None
    service_areas: Optional[list[StrictStr]] = None
    sms_signature: Optional[StrictStr] = Field(None, max_length=320)
    style_config: Optional[CommunicationStyle] = None
    style_instructions: Optional[StrictStr] = Field(None, max_length=2000)
    preferred_language: Optional[StrictStr] = Field(
        None,
        min_length=2,
        max_length=40,
        regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$",
    )

    _no_nulls = root_validator(pre=True, allow_reuse=True)(
        lambda cls, values: _reject_nulls(values, frozenset({"professional_title"}))
    )

    @validator(
        "full_name",
        "display_name",
        "professional_role",
        "professional_title",
        "brokerage_name",
        "sms_signature",
        "preferred_language",
        pre=True,
    )
    def _strip_single_line(cls, value):
        return _single_line(_clean_text(value))

    @validator("biography", "experience_summary", "style_instructions", pre=True)
    def _strip_multiline(cls, value):
        return _reject_control_characters(_clean_text(value))

    @validator("style_config", pre=True)
    def _style_must_be_object(cls, value):
        # Pydantic 1 would otherwise coerce lists/pairs into a dict.
        if not isinstance(value, dict):
            raise ValueError("style_config must be a JSON object")
        return value

    @validator("specialties", "service_areas")
    def _bounded_lists(cls, values):
        if values is None:
            return values
        return _clean_list(values, max_items=50, max_length=120)

    @validator("style_instructions")
    def _protect_platform_policy(cls, value):
        if value and _POLICY_OVERRIDE.search(value):
            raise ValueError(
                "Style instructions cannot override platform safety or disclosure policy"
            )
        return value


class PersonaSubmitRequest(StrictModel):
    """The owner submits one specific draft revision for HOB review."""

    revision: DbId


class PersonaPublishRequest(StrictModel):
    submission_id: DbId


class PersonaRejectionRequest(StrictModel):
    reason: StrictStr = Field(min_length=1, max_length=500)

    @validator("reason", pre=True)
    def _strip_reason(cls, value):
        return _reject_control_characters(_clean_text(value))


class PersonaDefaultUpdate(StrictModel):
    """Select (or clear, with explicit null) the caller's own default Persona.

    ``user_id`` is accepted only so a client that sends its own ID keeps
    working; any other user ID is refused.
    """

    persona_id: Optional[DbId] = Field(...)
    user_id: Optional[DbId] = None

    _no_nulls = root_validator(pre=True, allow_reuse=True)(
        lambda cls, values: _reject_nulls(values, frozenset({"persona_id"}))
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class PersonaVersionOut(BaseModel):
    id: int
    persona_id: int
    version_number: int
    full_name: str
    display_name: str
    professional_role: str
    professional_title: Optional[str]
    brokerage_name: str
    biography: str
    experience_summary: str
    specialties: list[str]
    service_areas: list[str]
    sms_signature: str
    style_config: dict
    style_instructions: str
    preferred_language: str
    content_hash: str
    style_schema_version: int
    prompt_schema_version: int
    published_by_user_id: int
    published_at: datetime

    class Config:
        orm_mode = True


class PersonaDraftOut(BaseModel):
    persona_id: int
    base_version_id: Optional[int]
    revision: int
    full_name: str
    display_name: str
    professional_role: str
    professional_title: Optional[str]
    brokerage_name: str
    biography: str
    experience_summary: str
    specialties: list[str]
    service_areas: list[str]
    sms_signature: str
    style_config: dict
    style_instructions: str
    preferred_language: str
    review_status: str
    submitted_by_user_id: Optional[int]
    submitted_at: Optional[datetime]
    last_editor_user_id: int
    updated_at: datetime

    class Config:
        orm_mode = True


class PersonaOut(BaseModel):
    id: int
    brokerage_id: str
    owner_user_id: int
    slug: str
    display_name: str
    scope: str
    status: str
    active_version_id: Optional[int]
    active_version_number: Optional[int] = None
    # Set when the owner left the brokerage; such a Persona is audit-only.
    membership_ended_at: Optional[datetime] = None
    created_by_user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


class PersonaDetail(PersonaOut):
    # Present only for the owner; always null for reviewers and other members.
    draft: Optional[PersonaDraftOut]
    active_version: Optional[PersonaVersionOut]


class PersonaValidationOut(BaseModel):
    valid: bool
    errors: list[str]
    warnings: list[str] = Field(default_factory=list)
    revision: int


class PersonaDefaultOut(BaseModel):
    channel: str
    user_id: int
    persona: Optional[PersonaOut]
    resolved_version: Optional[PersonaVersionOut]


class PersonaEventOut(BaseModel):
    id: int
    persona_id: int
    persona_version_id: Optional[int]
    event_type: str
    actor_user_id: Optional[int]
    from_state: Optional[str]
    to_state: Optional[str]
    detail: dict
    created_at: datetime

    class Config:
        orm_mode = True


class PersonaSubmissionOut(BaseModel):
    id: int
    persona_id: int
    submission_number: int
    draft_revision: int
    snapshot: dict
    content_hash: str
    status: str
    submitted_by_user_id: int
    submitted_at: datetime
    reviewed_by_user_id: Optional[int]
    reviewed_at: Optional[datetime]
    rejection_reason: Optional[str]
    published_version_id: Optional[int]

    class Config:
        orm_mode = True
