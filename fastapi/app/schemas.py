from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional
from datetime import datetime

# Roles a Head of Brokerage is allowed to hand out through an invitation.
INVITABLE_ROLES = ('broker', 'agent')

class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str]

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

class RegistrationResponse(BaseModel):
    message: str
    email: EmailStr

class InvitationCreate(BaseModel):
    email: EmailStr
    role: str

    @validator('role')
    def role_must_be_invitable(cls, value: str) -> str:
        normalized = (value or '').strip().lower()
        if normalized not in INVITABLE_ROLES:
            raise ValueError('You can only invite an Area Broker or an Agent.')
        return normalized

class InvitationResponse(BaseModel):
    message: str

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
                 'not_interested', 'no_response', 'dnc')

# The decision recorded on a replied lead.
FOLLOWUP_STATES = ('pending', 'accepted', 'declined')

class FollowUpOut(BaseModel):
    """One conversation the owner has replied to, with the reason it is waiting."""
    id: int
    contact: str
    name: Optional[str]
    property_address: Optional[str]
    area: Optional[str] = None
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
    # Text the owner the confirmation and the meeting link.
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

class TokenData(BaseModel):
    user_id: Optional[int]
    role: Optional[str]
