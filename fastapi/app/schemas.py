from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Roles a Head of Brokerage is allowed to hand out through an invitation.
INVITABLE_ROLES = ('broker', 'agent')

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str]
    timezone: Optional[str] = Field(default=None, max_length=100)

    @validator('timezone')
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('Use a valid IANA timezone, such as Asia/Karachi.')
        return value

class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: str
    last_name: str
    brokerage_name: str
    is_head_or_owner: bool

class UserOut(BaseModel):
    id: int
    email: EmailStr
    full_name: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    brokerage_name: Optional[str]
    brokerage_id: Optional[str]
    is_head_or_owner: Optional[bool]
    is_verified: Optional[bool]
    is_active: Optional[bool]
    role: str
    created_at: Optional[datetime]
    timezone: Optional[str] = None

    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str = 'bearer'

class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    expires_in: int
    user: UserOut

class RefreshRequest(BaseModel):
    refresh_token: str

class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class PasswordResetRequest(BaseModel):
    token: str = Field(min_length=1)
    password: str = Field(min_length=8)

class MessageResponse(BaseModel):
    message: str

class RegistrationResponse(BaseModel):
    message: str
    email: EmailStr

class InvitationCreate(BaseModel):
    email: EmailStr
    role: str
    broker_id: Optional[int] = None

    @validator('role')
    def role_must_be_invitable(cls, value: str) -> str:
        normalized = (value or '').strip().lower()
        if normalized not in INVITABLE_ROLES:
            raise ValueError('You can only invite an Area Broker or an Agent.')
        return normalized

class InvitationResponse(BaseModel):
    message: str

class DirectoryMemberResponse(BaseModel):
    id: int
    full_name: Optional[str]
    email: EmailStr
    role: str
    is_active: bool
    assigned_broker_id: Optional[int] = None

    class Config:
        orm_mode = True

class TeamDirectoryResponse(BaseModel):
    members: List[DirectoryMemberResponse]

class LeadAssignmentRequest(BaseModel):
    agent_id: Optional[int] = None

class LeadAssignmentStageRequest(BaseModel):
    stage: str

    @validator('stage')
    def stage_must_be_known(cls, value: str) -> str:
        normalized = (value or '').strip().lower().replace(' ', '_')
        allowed = {'new', 'processing', 'want_more_info', 'interested', 'ready_to_sell',
                   'location_discussion', 'not_interested', 'no_response', 'dnc'}
        if normalized not in allowed:
            raise ValueError('Choose New or a valid lead status.')
        return normalized

class InvitationValidationResponse(BaseModel):
    valid: bool
    email: Optional[EmailStr] = None
    role: Optional[str] = None
    role_label: Optional[str] = None
    brokerage_name: Optional[str] = None
    expires_at: Optional[datetime] = None
    message: Optional[str] = None

class InvitationAcceptRequest(BaseModel):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8)

    @validator('first_name', 'last_name')
    def name_must_not_be_blank(cls, value: str) -> str:
        cleaned = (value or '').strip()
        if not cleaned:
            raise ValueError('This field is required.')
        return cleaned

class InvitationAcceptResponse(AuthResponse):
    message: str

PHONE_PATTERN = r'^\+[1-9]\d{6,14}$'

class SmsConversationCreate(BaseModel):
    """Start a Bobbie outreach conversation for one property owner."""
    contact: str = Field(regex=PHONE_PATTERN, description='Owner phone number in E.164 format')
    name: str = Field(min_length=1, max_length=120)
    property_address: str = Field(min_length=1, max_length=300)
    outreach_reason: str = Field(min_length=1, max_length=300)
    # Bobbie sends the introduction and answers real inbound replies.
    ai_enabled: bool = True

class SmsConversationUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=120)
    property_address: Optional[str] = Field(None, max_length=300)
    ai_enabled: Optional[bool] = None

class SmsMessageOut(BaseModel):
    id: int
    direction: str
    from_number: Optional[str]
    to_number: Optional[str]
    text: Optional[str]
    status: Optional[str]
    event_type: Optional[str]
    created_at: Optional[datetime]

    class Config:
        orm_mode = True

class SmsConversationOut(BaseModel):
    id: int
    # The lead pool row behind the thread, when it came from an import. Null for
    # a thread the broker started by hand, which has no property record to show.
    lead_id: Optional[int] = None
    contact: str
    name: Optional[str]
    property_address: Optional[str]
    ai_enabled: bool
    handled_by: str
    awaiting_broker_reply: bool
    lead_status: str
    queue_status: str
    dnc_alert: bool
    meeting_booked: bool
    created_at: Optional[datetime]
    latest_message: Optional[str] = None
    latest_message_at: Optional[datetime] = None
    message_count: int = 0

    class Config:
        orm_mode = True

class SmsSendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1600)

class SmsConversationCreated(BaseModel):
    conversation: SmsConversationOut
    started: bool
    text: Optional[str] = None

# Statuses the assignee may set by hand from the Follow-ups panel.
LEAD_STATUSES = ('processing', 'want_more_info', 'interested', 'ready_to_sell',
                 'location_discussion', 'not_interested', 'no_response', 'dnc')

# The decision recorded on a replied lead.
FOLLOWUP_STATES = ('pending', 'accepted', 'declined')

class FollowUpPropertyOut(BaseModel):
    lead_id: int
    address: Optional[str] = None
    area: Optional[str] = None
    campaign_id: Optional[int] = None
    campaign_name: Optional[str] = None
    signals: list[str] = Field(default_factory=list)

class FollowUpOut(BaseModel):
    """One conversation the owner has replied to, with the reason it is waiting."""
    id: int
    # The lead pool row behind the thread, for the property details panel.
    lead_id: Optional[int] = None
    # The campaign that opened this thread, when one did.
    campaign_id: Optional[int] = None
    campaign_name: Optional[str] = None
    contact: str
    name: Optional[str]
    property_address: Optional[str]
    area: Optional[str] = None
    properties: list[FollowUpPropertyOut] = Field(default_factory=list)
    has_multiple_properties: bool = False
    ai_enabled: bool
    handled_by: str
    awaiting_broker_reply: bool
    lead_status: str
    queue_status: str
    dnc_alert: bool
    meeting_booked: bool
    followup_state: str
    reason: str
    reason_label: str
    waiting_days: Optional[int] = None
    reply_count: int = 0
    message_count: int = 0
    first_reply_at: Optional[datetime] = None
    last_reply_at: Optional[datetime] = None
    latest_message: Optional[str] = None
    latest_message_at: Optional[datetime] = None
    created_at: Optional[datetime]

class FollowUpStateRequest(BaseModel):
    state: str

    @validator('state')
    def state_must_be_known(cls, value: str) -> str:
        normalized = (value or '').strip().lower()
        if normalized not in FOLLOWUP_STATES:
            raise ValueError(f"State must be one of: {', '.join(FOLLOWUP_STATES)}.")
        return normalized

class FollowUpStatusUpdate(BaseModel):
    lead_status: str

    @validator('lead_status')
    def status_must_be_known(cls, value: str) -> str:
        normalized = (value or '').strip().lower()
        if normalized not in LEAD_STATUSES:
            raise ValueError(f"Lead status must be one of: {', '.join(LEAD_STATUSES)}.")
        return normalized

class FollowUpAppointmentRequest(BaseModel):
    """Book one of the broker's open slots for this owner."""
    start_at: datetime
    end_at: datetime
    title: Optional[str] = Field(None, max_length=200)
    address: str = Field(..., min_length=5, max_length=500)
    # Text the owner the confirmation and the appointment map link.
    notify: bool = True

class FollowUpAppointmentResult(BaseModel):
    booking: dict
    notified: bool
    note: Optional[str] = None
    followup: FollowUpOut

class LeadDeleteRequest(BaseModel):
    """Remove one or more leads from the broker's pool.

    The ceiling is a guard against a runaway request, not a product limit:
    "select all" on a large pool must go through in one call. The service
    chunks the ids before they reach the database.
    """
    lead_ids: List[int] = Field(min_items=1, max_items=100_000)

class LeadCampaignRequest(BaseModel):
    """Hand a selection of leads to Bobbie as one outreach batch.

    The real batch limit lives in the campaign service so an oversized
    selection gets a sentence explaining the cap, not a validation error.
    """
    lead_ids: List[int] = Field(min_items=1, max_items=100_000)
    # Optional: when blank, each lead's own signals supply the reason.
    outreach_reason: Optional[str] = Field(None, max_length=300)

class CampaignDraftRequest(BaseModel):
    """Turn a selection in the Lead Pool into a draft campaign to compose."""
    lead_ids: List[int] = Field(min_items=1, max_items=100_000)
    name: Optional[str] = Field(None, max_length=200)
    message_template: Optional[str] = Field(None, max_length=1600)

class CampaignUpdateRequest(BaseModel):
    """Rename a campaign, rewrite its message or its reason, or all three."""
    name: Optional[str] = Field(None, max_length=200)
    message_template: Optional[str] = Field(None, max_length=1600)
    # Empty string is meaningful: it hands {{reason}} back to each lead's signal.
    outreach_reason: Optional[str] = Field(None, max_length=300)

class CampaignSignalShare(BaseModel):
    """How much of the campaign carries one signal."""
    key: str
    label: str
    count: int
    share: float

class CampaignReasonSuggestions(BaseModel):
    """Reason wordings that fit what this set of leads has in common."""
    signals: List[CampaignSignalShare] = []
    suggestions: List[str] = []
    # 'ai' when DeepSeek phrased them, 'catalog' for the built-in wordings.
    source: str = 'catalog'
    # Why the catalog was used, when it was. Empty on the happy path.
    note: str = ''

class ReplySuggestions(BaseModel):
    """Draft next replies for a thread, grounded in its message history."""
    suggestions: List[str] = []
    # 'ai' when DeepSeek drafted them, 'template' for the built-in wordings.
    source: str = 'template'
    # Why the templates were used, when they were. Empty on the happy path.
    note: str = ''

class CampaignRecipient(BaseModel):
    """One lead of a campaign: what it would receive, or what it did receive.

    Carries the same fields the lead pool shows, because a campaigned lead is
    listed here instead of there and the table is the same table.
    """
    lead_id: int
    owner_name: Optional[str]
    phone: Optional[str]
    property_address: Optional[str]
    area: Optional[str] = None
    signals: List[dict] = []
    score: int = 0
    stage: Optional[str] = None
    last_activity_at: Optional[datetime] = None
    text: str
    is_long: bool = False
    # Set once the campaign has been sent, so its leads can be opened from here
    # — they no longer appear in the lead pool.
    conversation_id: Optional[int] = None
    replied: bool = False

class CampaignSkip(BaseModel):
    lead_id: int
    owner_name: Optional[str]
    reason: str

class CampaignPreview(BaseModel):
    campaign_id: int
    recipients: List[CampaignRecipient]
    skipped: List[CampaignSkip]
    # Set when the template will not render; the composer shows it and blocks Send.
    template_error: str = ''
    tokens: dict = {}

class CampaignOut(BaseModel):
    """A campaign and how its outreach is going."""
    id: int
    name: str
    message_template: str
    outreach_reason: str = ''
    status: str
    sent_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    recipients: int = 0
    delivered: int = 0
    replied: int = 0
    no_reply: int = 0
    not_sent: int = 0
    broker_id: Optional[int] = None
    broker_name: Optional[str] = None
    broker_email: Optional[EmailStr] = None
    broker_role: Optional[str] = None

class CampaignDetail(CampaignOut):
    preview: CampaignPreview

class CampaignDraftResult(BaseModel):
    # The full detail, so the composer opens without a second round trip.
    campaign: CampaignDetail
    # Selected leads the draft could not take, each with the reason.
    not_added: List[CampaignSkip] = []

class TokenData(BaseModel):
    user_id: Optional[int]
    role: Optional[str]
