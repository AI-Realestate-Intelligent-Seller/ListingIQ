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

from pydantic import BaseModel, Field, StrictBool, StrictStr, conint, root_validator, validator

# Largest value a PostgreSQL INTEGER column can hold. Identifiers outside this
# range are rejected up front instead of surfacing as a database error.
MAX_DB_INT = 2_147_483_647

# Strict integers: JSON strings, floats and booleans are rejected, not coerced.
DbId = conint(strict=True, ge=1, le=MAX_DB_INT)
StyleLevel = conint(strict=True, ge=1, le=5)
NonNegativeInt = conint(strict=True, ge=0, le=100)

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


class IdentityProfile(StrictModel):
    license_number: StrictStr = Field("", max_length=120)
    licensing_jurisdiction: StrictStr = Field("", max_length=120)
    licensing_status: StrictStr = Field("", max_length=80)
    public_email: StrictStr = Field("", max_length=254)
    public_phone: StrictStr = Field("", max_length=40)
    professional_intro: StrictStr = Field("", max_length=2000)
    brand_identity: StrictStr = Field("", max_length=1000)


class BackgroundProfile(StrictModel):
    years_experience: Optional[NonNegativeInt] = None
    previous_experience: StrictStr = Field("", max_length=5000)
    target_audiences: list[StrictStr] = Field(default_factory=list)
    industry_expertise: list[StrictStr] = Field(default_factory=list)
    differentiators: list[StrictStr] = Field(default_factory=list)
    unique_selling_proposition: StrictStr = Field("", max_length=2000)
    achievements: list[StrictStr] = Field(default_factory=list)
    transaction_experience: StrictStr = Field("", max_length=2000)
    transaction_claim_status: Literal["missing", "self_declared", "not_applicable"] = "missing"
    professional_philosophy: StrictStr = Field("", max_length=3000)
    brand_positioning: StrictStr = Field("", max_length=2000)
    business_background: StrictStr = Field("", max_length=3000)

    @validator("target_audiences", "industry_expertise", "differentiators", "achievements")
    def _bounded_items(cls, values):
        return _clean_list(values, max_items=50, max_length=240)


class CommunicationDNA(StrictModel):
    professional_casual_behavior: StrictStr = Field("", max_length=2000)
    confidence: StyleLevel = 3
    warmth: StyleLevel = 3
    directness: StyleLevel = 3
    formality: StyleLevel = 3
    empathy: StyleLevel = 3
    energy: StyleLevel = 3
    preferred_sms_length: Literal["brief", "balanced", "detailed"] = "brief"
    preferred_email_length: Literal["brief", "balanced", "detailed"] = "balanced"
    paragraph_style: StrictStr = Field("", max_length=500)
    questions_per_message: NonNegativeInt = 1
    emoji_usage: StrictStr = Field("", max_length=500)
    industry_terminology: StrictStr = Field("", max_length=1000)
    other_languages: list[StrictStr] = Field(default_factory=list)
    multilingual_preferences: StrictStr = Field("", max_length=1500)
    language_switching: StrictStr = Field("", max_length=1000)
    cultural_preferences: StrictStr = Field("", max_length=1500)
    signature_phrases: list[StrictStr] = Field(default_factory=list)
    unnatural_expressions: list[StrictStr] = Field(default_factory=list)
    brand_statements: list[StrictStr] = Field(default_factory=list)
    hesitant_response: StrictStr = Field("", max_length=2000)
    concept_explanation: StrictStr = Field("", max_length=2000)
    uncertainty_behavior: StrictStr = Field("", max_length=1500)
    negative_reply_behavior: StrictStr = Field("", max_length=1500)
    next_step_behavior: StrictStr = Field("", max_length=1500)
    human_involvement: StrictStr = Field("", max_length=1500)

    @validator("other_languages", "signature_phrases", "unnatural_expressions", "brand_statements")
    def _bounded_items(cls, values):
        return _clean_list(values, max_items=50, max_length=160)


class ConversationPlaybook(StrictModel):
    introduction: StrictStr = Field("", max_length=2000)
    trust_building: StrictStr = Field("", max_length=2000)
    qualifying_questions: StrictStr = Field("", max_length=2000)
    valuation_conversations: StrictStr = Field("", max_length=2000)
    buyer_vs_seller: StrictStr = Field("", max_length=2000)
    investors_and_owners: StrictStr = Field("", max_length=2000)
    pricing_disagreements: StrictStr = Field("", max_length=2000)
    objections: StrictStr = Field("", max_length=2000)
    follow_up: StrictStr = Field("", max_length=2000)
    appointments: StrictStr = Field("", max_length=2000)
    sensitive_escalation: StrictStr = Field("", max_length=2000)
    human_handoff: StrictStr = Field("", max_length=2000)


class SourceClaim(StrictModel):
    """A claim found in an imported document, kept with its source status.

    ``source_status`` records what the document said and never becomes
    "verified". ``owner_status`` is the owner's own review; owner confirmation
    is not independent verification.
    """

    statement: StrictStr = Field(min_length=1, max_length=500)
    source_status: Literal["self_reported", "unverified"]
    field_path: StrictStr = Field("", max_length=200)
    source_excerpt: StrictStr = Field("", max_length=500)
    owner_status: Literal["unreviewed", "owner_confirmed", "owner_disputed"] = "unreviewed"
    owner_note: StrictStr = Field("", max_length=500)


class SourceDetail(StrictModel):
    """Useful imported information with no matching profile field."""

    section: StrictStr = Field(min_length=1, max_length=120)
    detail: StrictStr = Field(min_length=1, max_length=1000)
    owner_status: Literal["unreviewed", "kept", "dismissed"] = "unreviewed"


class SourceEvidence(StrictModel):
    field_path: StrictStr = Field(min_length=1, max_length=200)
    source_excerpt: StrictStr = Field(min_length=1, max_length=500)
    confidence: Literal["high", "medium", "low"]
    needs_owner_confirmation: StrictBool = True


class SourceImport(StrictModel):
    job_id: StrictStr = Field(min_length=1, max_length=32)
    file_name: StrictStr = Field("", max_length=255)
    sha256: StrictStr = Field("", max_length=64)


class SourceReview(StrictModel):
    """What imported documents said, carried into submissions and versions.

    The server writes this when an import is applied. Owner saves may change
    only ``owner_status`` and ``owner_note``; see ``service.update_draft``.
    """

    imports: list[SourceImport] = Field(default_factory=list, max_items=20)
    claims: list[SourceClaim] = Field(default_factory=list, max_items=200)
    unmapped_details: list[SourceDetail] = Field(default_factory=list, max_items=200)
    evidence: list[SourceEvidence] = Field(default_factory=list, max_items=200)
    missing_questions: list[StrictStr] = Field(default_factory=list, max_items=200)
    contradictions: list[StrictStr] = Field(default_factory=list, max_items=200)
    completeness_warnings: list[StrictStr] = Field(default_factory=list, max_items=200)

    @validator("missing_questions", "contradictions", "completeness_warnings")
    def _bounded_text(cls, values):
        return _clean_list(values, max_items=200, max_length=500)


class PersonaProfileConfig(StrictModel):
    schema_version: Literal[2] = 2
    completeness_enforced: StrictBool = False
    identity: IdentityProfile = Field(default_factory=IdentityProfile)
    background: BackgroundProfile = Field(default_factory=BackgroundProfile)
    communication: CommunicationDNA = Field(default_factory=CommunicationDNA)
    playbook: ConversationPlaybook = Field(default_factory=ConversationPlaybook)
    # Authoritative verification is deliberately not client-settable. A future
    # verification workflow may add that status server-side after checking a source.
    confirmations: dict[StrictStr, Literal["missing", "self_declared", "owner_confirmed", "not_applicable"]] = Field(default_factory=dict)
    na_reasons: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    source_review: SourceReview = Field(default_factory=SourceReview)

    @validator("confirmations")
    def _bounded_confirmations(cls, value):
        if len(value) > 100:
            raise ValueError("At most 100 confirmations are allowed")
        return value

    @validator("na_reasons")
    def _bounded_na_reasons(cls, value):
        if len(value) > 100 or any(len(str(item)) > 500 for item in value.values()):
            raise ValueError("Not-applicable reasons must be 500 characters or fewer")
        return value


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
    profile_config: Optional[PersonaProfileConfig] = None

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

    @validator("style_config", "profile_config", pre=True)
    def _config_must_be_object(cls, value):
        # Pydantic 1 would otherwise coerce lists/pairs into a dict.
        if not isinstance(value, dict):
            raise ValueError("configuration must be a JSON object")
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


class PersonaExampleCreate(StrictModel):
    revision: DbId
    scenario: StrictStr = Field(min_length=1, max_length=120)
    channel: Literal["sms", "email"]
    language: StrictStr = Field("en", min_length=2, max_length=40, regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$")
    # Empty when the owner starts the conversation (for example an introduction).
    customer_message: StrictStr = Field("", max_length=5000)
    preferred_response: StrictStr = Field(min_length=1, max_length=10000)
    context: StrictStr = Field("", max_length=3000)
    style_explanation: StrictStr = Field("", max_length=3000)
    provenance: Literal["authentic", "user_written", "illustrative"]
    owner_approved: StrictBool = False


class PersonaExampleUpdate(StrictModel):
    revision: DbId
    scenario: Optional[StrictStr] = Field(None, min_length=1, max_length=120)
    channel: Optional[Literal["sms", "email"]] = None
    language: Optional[StrictStr] = Field(None, min_length=2, max_length=40, regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$")
    customer_message: Optional[StrictStr] = Field(None, max_length=5000)
    preferred_response: Optional[StrictStr] = Field(None, min_length=1, max_length=10000)
    context: Optional[StrictStr] = Field(None, max_length=3000)
    style_explanation: Optional[StrictStr] = Field(None, max_length=3000)
    provenance: Optional[Literal["authentic", "user_written", "illustrative"]] = None
    owner_approved: Optional[StrictBool] = None


class PersonaExampleBatchItem(StrictModel):
    """One example as it stands in the owner's form; ``id`` for an existing example."""

    id: Optional[DbId] = None
    scenario: StrictStr = Field(min_length=1, max_length=120)
    channel: Literal["sms", "email"]
    language: StrictStr = Field("en", min_length=2, max_length=40, regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$")
    customer_message: StrictStr = Field("", max_length=5000)
    preferred_response: StrictStr = Field(min_length=1, max_length=10000)
    # New examples typed into the form are the owner's own writing.
    provenance: Optional[Literal["authentic", "user_written", "illustrative"]] = None

    @validator("scenario", "preferred_response", pre=True)
    def _not_blank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank")
        return value


class PersonaExampleBatch(StrictModel):
    """The complete set of examples saved from the How I Communicate step."""

    revision: DbId
    examples: list[PersonaExampleBatchItem] = Field(default_factory=list, max_items=1000)


class PersonaExampleMutation(StrictModel):
    revision: DbId


class PersonaSubmitRequest(StrictModel):
    """The owner submits one specific draft revision for HOB review."""

    revision: DbId


class PersonaPublishRequest(StrictModel):
    submission_id: DbId


class PersonaSelfPublishRequest(StrictModel):
    """The owner publishes the saved draft revision they reviewed."""

    revision: DbId


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
    profile_config: dict = Field(default_factory=dict)
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
    profile_config: dict = Field(default_factory=dict)
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
    sections: dict[str, dict] = Field(default_factory=dict)
    missing_count: int = 0


class PersonaExampleOut(BaseModel):
    id: int
    position: int
    scenario: str
    channel: str
    language: str
    customer_message: str
    preferred_response: str
    context: str
    style_explanation: str
    provenance: str
    owner_approved: bool
    review_flags: list[str] = Field(default_factory=list)
    revision: Optional[int] = None

    class Config:
        orm_mode = True


class PersonaExamplePage(BaseModel):
    items: list[PersonaExampleOut]
    total: int
    page: int
    page_size: int
    revision: Optional[int] = None


class ExtractionEvidence(StrictModel):
    field_path: StrictStr = Field(min_length=1, max_length=200)
    source_excerpt: StrictStr = Field(min_length=1, max_length=500)
    confidence: Literal["high", "medium", "low"]
    needs_owner_confirmation: StrictBool = True


class ExtractedExample(StrictModel):
    scenario: StrictStr = Field(min_length=1, max_length=120)
    channel: Literal["sms", "email"]
    language: StrictStr = Field("en", min_length=2, max_length=40)
    # Empty only for first-touch outreach, where no customer message exists.
    customer_message: StrictStr = Field("", max_length=5000)
    preferred_response: StrictStr = Field(min_length=1, max_length=10000)
    context: StrictStr = Field("", max_length=3000)
    style_explanation: StrictStr = Field("", max_length=3000)
    provenance: Literal["authentic", "user_written", "illustrative"]
    # Reasons the owner must review this example before relying on it.
    review_flags: list[StrictStr] = Field(default_factory=list)

    @validator("review_flags")
    def _bounded_flags(cls, values):
        return _clean_list(values, max_items=10, max_length=300)


class ExtractedClaim(StrictModel):
    """A factual claim about the person that the source does not prove."""

    statement: StrictStr = Field(min_length=1, max_length=500)
    status: Literal["self_reported", "unverified"]
    field_path: StrictStr = Field("", max_length=200)
    source_excerpt: StrictStr = Field("", max_length=500)


class ExtractedDetail(StrictModel):
    """Useful source information with no matching draft field."""

    section: StrictStr = Field(min_length=1, max_length=120)
    detail: StrictStr = Field(min_length=1, max_length=1000)


_EXTRACTED_DRAFT_FIELDS = frozenset({
    "full_name", "display_name", "professional_role", "professional_title",
    "biography", "experience_summary", "specialties", "service_areas",
    "sms_signature", "preferred_language", "style_instructions",
})


class PersonaExtractionContract(StrictModel):
    draft: dict = Field(default_factory=dict)
    style_config: dict = Field(default_factory=dict)
    profile_config: dict = Field(default_factory=dict)
    examples: list[ExtractedExample] = Field(default_factory=list)
    evidence: list[ExtractionEvidence] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    unmapped_details: list[ExtractedDetail] = Field(default_factory=list)
    missing_questions: list[StrictStr] = Field(default_factory=list)
    contradictions: list[StrictStr] = Field(default_factory=list)
    unsupported_claims: list[StrictStr] = Field(default_factory=list)

    @validator("draft")
    def _strict_draft_fields(cls, value):
        unknown = set(value) - _EXTRACTED_DRAFT_FIELDS
        if unknown:
            raise ValueError(f"Unknown extracted draft fields: {', '.join(sorted(unknown))}")
        # Reuse the real draft contract for every supplied value.
        PersonaDraftUpdate.parse_obj({"revision": 1, **value})
        return value

    @validator("style_config")
    def _strict_style_fields(cls, value):
        if not isinstance(value, dict):
            raise ValueError("style_config must be an object")
        unknown = set(value) - set(CommunicationStyle.__fields__)
        if unknown:
            raise ValueError(f"Unknown style fields: {', '.join(sorted(unknown))}")
        CommunicationStyle.parse_obj(value)
        return value

    @validator("profile_config")
    def _strict_profile_fields(cls, value):
        if not isinstance(value, dict):
            raise ValueError("profile_config must be an object")
        unknown = set(value) - (set(PersonaProfileConfig.__fields__) - _WIRE_EXCLUDED_PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"Unknown profile fields: {', '.join(sorted(unknown))}")
        PersonaProfileConfig.parse_obj(value)
        return value

    @validator("examples")
    def _reasonable_example_safeguard(cls, value):
        if len(value) > 1000:
            raise ValueError("At most 1000 examples may be extracted from one document")
        return value

    @validator("claims", "unmapped_details")
    def _bounded_records(cls, value):
        if len(value) > 100:
            raise ValueError("At most 100 entries are allowed")
        return value

    @validator("missing_questions", "contradictions", "unsupported_claims")
    def _bounded_assessment(cls, values):
        return _clean_list(values, max_items=100, max_length=500)


# Profile keys the extractor never sets: confirmation status is decided by the
# server and the owner, and the schema/enforcement flags are platform state.
_WIRE_EXCLUDED_PROFILE_FIELDS = frozenset({
    "schema_version", "completeness_enforced", "confirmations", "na_reasons", "source_review",
})
# Keywords AI providers accept in a response schema. Others (defaults, regex
# patterns, titles) are dropped; length limits are kept and restated by the
# provider adapter where the provider cannot enforce them. Pydantic still
# validates every returned value against the full contract.
_WIRE_KEYWORDS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "description", "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
})


def _unstated_scalar(result: dict) -> dict:
    """Let a patch scalar say "the document does not state this" with "".

    Every scalar is sent as a plain string so the schema has no null values,
    union types or large enums, matching the small typed schemas that
    structured outputs compile reliably. ``from_wire`` converts numbers back.
    """
    description = result.get("description", "")
    if "enum" in result:
        return {"type": "string", "enum": [str(item) for item in result["enum"]] + [""],
                **({"description": description} if description else {})}
    if result.get("type") == "integer":
        bounds = f"a whole number from {result.get('minimum', 0)} to {result.get('maximum', '')}".strip()
        return {"type": "string", "description": f"{description} {bounds}, or empty if not stated".strip()}
    if result.get("type") == "boolean":
        return {"type": "string", "enum": ["true", "false", ""]}
    return result


def from_wire(value, model):
    """Convert string-encoded wire scalars back to the model's integer/boolean types."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    for name, item in value.items():
        field = model.__fields__.get(name)
        if field is None:
            continue
        kind = field.type_
        if isinstance(kind, type) and issubclass(kind, BaseModel) and isinstance(item, dict):
            result[name] = from_wire(item, kind)
        elif isinstance(item, str) and isinstance(kind, type) and issubclass(kind, bool):
            result[name] = {"true": True, "false": False}.get(item.strip().lower(), item)
        elif isinstance(item, str) and isinstance(kind, type) and issubclass(kind, int) and item.strip().isdigit():
            result[name] = int(item.strip())
        elif isinstance(item, str) and getattr(field.outer_type_, "__origin__", None) is Literal:
            options = field.outer_type_.__args__
            match = next((option for option in options if str(option) == item), None)
            if match is not None:
                result[name] = match
    return result


def _wire(schema: dict, definitions: dict, *, unstated: bool = False) -> dict:
    """Convert a Pydantic v1 property schema into a closed, all-required wire schema.

    Every property is required because structured outputs allow at most 24
    optional properties per request. In draft/profile patches (``unstated``)
    a value the document does not state is "" or [] for text and lists and
    null for other scalars; ``strip_unstated`` removes them before validation.
    """
    if "$ref" in schema:
        return _wire(definitions[schema["$ref"].rsplit("/", 1)[-1]], definitions, unstated=unstated)
    if "allOf" in schema and len(schema["allOf"]) == 1:
        merged = {key: value for key, value in schema.items() if key != "allOf"}
        return _wire({**_wire(schema["allOf"][0], definitions, unstated=unstated), **merged}, definitions, unstated=unstated)
    result = {}
    for key, value in schema.items():
        if key == "const":
            result["enum"] = [value]
        elif key == "properties":
            result[key] = {name: _wire(child, definitions, unstated=unstated) for name, child in value.items()}
        elif key == "items":
            result[key] = _wire(value, definitions)
        elif key in _WIRE_KEYWORDS:
            result[key] = value
    if result.get("type") == "object":
        if not isinstance(result.get("properties"), dict):
            raise ValueError("Wire schema objects must declare their properties")
        result["additionalProperties"] = False
        result["required"] = list(result["properties"])
    elif unstated:
        result = _unstated_scalar(result)
    return result


def _object_schema(model, *, exclude=frozenset(), only=None, unstated: bool = False) -> dict:
    full = model.schema()
    definitions = full.get("definitions", {})
    properties = {
        name: _wire(child, definitions, unstated=unstated)
        for name, child in full["properties"].items()
        if name not in exclude and (only is None or name in only)
    }
    return {"type": "object", "properties": properties, "additionalProperties": False, "required": list(properties)}


def strip_unstated(value):
    """Drop null, empty text, empty lists and emptied objects from a patch."""
    if isinstance(value, dict):
        cleaned = {key: strip_unstated(item) for key, item in value.items()}
        return {key: item for key, item in cleaned.items() if item not in (None, "", [], {})}
    if isinstance(value, list):
        return [item for item in (strip_unstated(entry) for entry in value) if item not in (None, "")]
    if isinstance(value, str):
        return value if value.strip() else ""
    return value


def extraction_wire_schema() -> dict:
    """The provider response schema for document extraction.

    It mirrors ``PersonaExtractionContract`` with every object spelled out and
    closed (``additionalProperties: false``) and no ``$ref`` or free-form maps,
    which structured-output providers reject.
    """
    draft = _object_schema(PersonaDraftUpdate, only=_EXTRACTED_DRAFT_FIELDS, unstated=True)
    style = _object_schema(CommunicationStyle, unstated=True)
    profile = _object_schema(PersonaProfileConfig, exclude=_WIRE_EXCLUDED_PROFILE_FIELDS, unstated=True)
    text_list = {"type": "array", "items": {"type": "string", "maxLength": 500}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "draft", "style_config", "profile_config", "examples", "evidence", "claims",
            "unmapped_details", "missing_questions", "contradictions", "unsupported_claims",
        ],
        "properties": {
            "draft": draft,
            "style_config": style,
            "profile_config": profile,
            "examples": {"type": "array", "items": _object_schema(ExtractedExample)},
            "evidence": {"type": "array", "items": _object_schema(ExtractionEvidence)},
            "claims": {"type": "array", "items": _object_schema(ExtractedClaim)},
            "unmapped_details": {"type": "array", "items": _object_schema(ExtractedDetail)},
            "missing_questions": text_list,
            "contradictions": text_list,
            "unsupported_claims": text_list,
        },
    }


class ReviewedImportDraft(StrictModel):
    """Visible draft fields as the owner left them in the auto-filled form."""

    full_name: Optional[StrictStr] = Field(None, min_length=1, max_length=255)
    professional_title: Optional[StrictStr] = Field(None, max_length=160)
    biography: Optional[StrictStr] = Field(None, max_length=5000)
    specialties: Optional[list[StrictStr]] = None
    service_areas: Optional[list[StrictStr]] = None
    preferred_language: Optional[StrictStr] = Field(None, min_length=2, max_length=40, regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$")


class ReviewedImportExample(StrictModel):
    """One example as the owner edited it; ``source_index`` points at the imported original."""

    source_index: Optional[int] = Field(None, ge=0, le=999)
    scenario: StrictStr = Field(min_length=1, max_length=120)
    channel: Literal["sms", "email"]
    language: StrictStr = Field("en", min_length=2, max_length=40, regex=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8})?$")
    customer_message: StrictStr = Field("", max_length=5000)
    preferred_response: StrictStr = Field(min_length=1, max_length=10000)
    provenance: Literal["authentic", "user_written", "illustrative"]
    # Accepted for older clients and ignored: saving the reviewed form approves every included example.
    owner_approved: StrictBool = False

    @validator("scenario", "preferred_response", pre=True)
    def _not_blank(cls, value):
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank")
        return value


class ReviewedImport(StrictModel):
    """The owner's reviewed version of an import proposal.

    Only fields the auto-filled form shows are accepted; everything else in
    the proposal is applied by the server without overwriting saved values.
    """

    draft: ReviewedImportDraft = Field(default_factory=ReviewedImportDraft)
    # identity, background audiences/differentiators, communication.other_languages, confirmations, na_reasons.
    profile: dict = Field(default_factory=dict)
    examples: list[ReviewedImportExample] = Field(default_factory=list, max_items=1000)
    # Owner decisions on imported claims and unmapped details (statuses and notes only).
    source_review: dict = Field(default_factory=dict)

    @validator("profile")
    def _visible_profile_only(cls, value):
        allowed = {"identity", "background", "communication", "confirmations", "na_reasons"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"Unsupported profile fields: {', '.join(sorted(unknown))}")
        if set((value.get("communication") or {})) - {"other_languages"}:
            raise ValueError("Only other_languages may be reviewed in communication")
        if set((value.get("background") or {})) - {"target_audiences", "differentiators"}:
            raise ValueError("Only target_audiences and differentiators may be reviewed in background")
        return value


class PersonaImportApply(StrictModel):
    revision: DbId
    # Absent: apply the proposal as extracted (legacy behaviour). Present: apply
    # exactly what the owner reviewed in the auto-filled form.
    reviewed: Optional[ReviewedImport] = None


class PersonaImportStageOut(BaseModel):
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    message: Optional[str] = None


class PersonaImportJobOut(BaseModel):
    id: str
    persona_id: int
    base_revision: int
    file_name: str
    file_size: int
    file_hash: str
    storage_backend: str = "database"
    status: str
    current_stage: Optional[str]
    stages: dict[str, PersonaImportStageOut]
    proposal: Optional[dict]
    assessment: Optional[dict]
    error_code: Optional[str]
    error_message: Optional[str]
    retryable: bool
    attempt_count: int
    extraction_mode: str = "local"
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]
    applied_at: Optional[datetime]

    class Config:
        orm_mode = True


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
