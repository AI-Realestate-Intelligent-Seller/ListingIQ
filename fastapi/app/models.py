from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    Index,
    event,
    inspect,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .db import Base

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    brokerage_name = Column(String(255))
    brokerage_id = Column(String(255), nullable=True)
    is_head_or_owner = Column(Boolean, default=False)
    is_verified = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    role = Column(String(50), default='user')
    timezone = Column(String(100),nullable=True,)
    # Agents are assigned to one Area Broker by the HOB who invites them.
    assigned_broker_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    google_refresh_token = Column(Text, nullable=True)  # encrypted
    created_at = Column(DateTime, default=datetime.utcnow)
    assigned_broker = relationship('User', remote_side=[id], foreign_keys=[assigned_broker_id])


class FeatureFlag(Base):
    """A platform-owned product switch, optionally scoped to one brokerage."""
    __tablename__ = 'feature_flags'
    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), nullable=False, index=True)
    description = Column(String(500), nullable=True)
    enabled = Column(Boolean, nullable=False, default=False)
    brokerage_id = Column(String(255), nullable=True, index=True)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PlatformAuditLog(Base):
    """Append-only record of privileged internal actions."""
    __tablename__ = 'platform_audit_logs'
    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    target_type = Column(String(50), nullable=False)
    target_id = Column(String(255), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

class Invitation(Base):
    __tablename__ = 'invitations'
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    role = Column(String(50), nullable=False)
    brokerage_id = Column(String(255), nullable=False, index=True)
    brokerage_name = Column(String(255))
    invited_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    assigned_broker_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String(50), nullable=False, default='pending')
    accepted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    invited_by_user = relationship('User', foreign_keys=[invited_by])
    assigned_broker = relationship('User', foreign_keys=[assigned_broker_id])

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, primary_key=True, index=True)
    contact = Column(String(50), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String(255))
    property_address = Column(String(500))
    # Normalised form of property_address (see app.leads.address). Stored rather
    # than computed per query so the cross-brokerage property claim is one
    # indexed lookup instead of a scan.
    property_key = Column(String(500), nullable=True, index=True)
    # Bobbie autopilot. On by default for new outreach.
    ai_enabled = Column(Boolean, nullable=False, default=True)
    # Retained for existing rows; the simulated recipient has been removed.
    recipient_ai_enabled = Column(Boolean, nullable=False, default=False)
    # Who owns the next reply: 'bobbie' while she is driving, 'broker' once she
    # has stopped. A broker-owned thread never triggers an automatic reply.
    handled_by = Column(String(20), nullable=False, default='bobbie')
    # Approved lead facts Bobbie is allowed to ground her replies in (JSON text).
    lead_context = Column(Text, nullable=True)
    # The campaign that opened this thread, when one did. Null for a thread the
    # broker started by hand from the SMS tab.
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=True, index=True)
    lead_status = Column(String(50), nullable=False, default='processing')
    queue_status = Column(String(50), nullable=False, default='idle')
    dnc_alert = Column(Boolean, nullable=False, default=False)
    meeting_booked = Column(Boolean, nullable=False, default=False)
    # The Follow-ups decision on a lead who replied: 'pending' until the
    # assignee accepts or declines it.
    followup_state = Column(String(20), nullable=False, default='pending')
    # Persisted Bobbie no-response cadence. A null due time means no proactive
    # follow-up is scheduled (owner replied, human takeover, or terminal state).
    followup_attempt_count = Column(Integer, nullable=False, default=0)
    followup_started_at = Column(DateTime, nullable=True)
    next_followup_at = Column(DateTime, nullable=True, index=True)
    final_followup_sent_at = Column(DateTime, nullable=True)
    dead_at = Column(DateTime, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User')

class Message(Base):
    __tablename__ = 'messages'

    id = Column(Integer, primary_key=True, index=True)

    conversation_id = Column(
        Integer,
        ForeignKey('conversations.id'),
        index=True,
    )

    direction = Column(String(20))

    from_number = Column(String(50))
    to_number = Column(String(50))

    text = Column(Text)
    status = Column(String(50))
    event_type = Column(String(100), index=True)

    # NEW:
    # Exact human user who sent this message.
    # NULL for AI, customer, system and historical messages.
    sender_user_id = Column(
        Integer,
        ForeignKey('users.id'),
        nullable=True,
        index=True,
    )

    campaign_id = Column(
        Integer,
        ForeignKey('campaigns.id'),
        nullable=True,
        index=True,
    )

    property_key = Column(
        String(500),
        nullable=True,
        index=True,
    )

    telnyx_id = Column(
        String(128),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow,
        index=True,
    )

    conversation = relationship('Conversation')

    sender_user = relationship(
        'User',
        foreign_keys=[sender_user_id],
    )

class Booking(Base):
    __tablename__ = 'bookings'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), index=True)
    phone = Column(String(50))
    name = Column(String(255), nullable=True)
    title = Column(String(255))
    location_address = Column(String(500), nullable=True)
    start_at = Column(DateTime)
    end_at = Column(DateTime)
    join_token = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    """A named batch of outreach: one message template sent to many leads.

    A campaign is drafted before it is sent. `create campaign` on a selection
    in the Lead Pool creates a draft holding those leads, and the broker names
    it and writes the opening message in the Campaigns tab. Sending renders the
    template once per lead and opens each conversation in user-handled mode.

    The template is the broker's own copy, with `{{token}}` placeholders that
    app.leads.template fills from the lead — the address and the reason differ
    per recipient, so the message cannot be a fixed string.
    """
    __tablename__ = 'campaigns'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(200), nullable=False, default='')
    message_template = Column(Text, nullable=False, default='')
    # One reason for the whole batch, filling {{reason}} for every recipient.
    # Blank falls back to each lead's own signal, which is the right default
    # for a mixed selection but wrong for a set that shares one story.
    outreach_reason = Column(String(300), nullable=True)
    # 'draft' until the broker sends it, 'sent' afterwards. A sent campaign is
    # a record: its name and template stay editable but it never sends twice.
    status = Column(String(20), nullable=False, default='draft')
    sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User')


class Lead(Base):
    """One prospect in a broker's lead pool, imported from a CSV.

    Owner and property come from the import; signals, score, stage and last
    activity are maintained by the system. A lead becomes a conversation when
    the broker adds it to a campaign, and `conversation_id` links the two.
    """
    __tablename__ = 'leads'
    __table_args__ = (Index('ix_leads_coordinates', 'latitude', 'longitude'),)
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    owner_name = Column(String(255))
    phone = Column(String(50), index=True)
    property_address = Column(String(500))
    area = Column(String(120))
    # Coordinates are populated asynchronously by app.location.worker.  They
    # belong to the lead (rather than a transient map response), so map views
    # never need to geocode while rendering.
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    geocoding_status = Column(String(24), nullable=False, default='pending', index=True)
    geocoding_provider = Column(String(50), nullable=True)
    geocoded_at = Column(DateTime, nullable=True)
    geocoding_error = Column(String(500), nullable=True)
    geocoding_retry_count = Column(Integer, nullable=False, default=0)
    normalized_address = Column(String(700), nullable=True)
    # Canonical signal keys, comma separated (see app.leads.catalog).
    signals = Column(String(500), nullable=False, default='')
    # The vendor's own words for why this owner is being contacted. Bobbie uses
    # it verbatim rather than inventing a reason.
    outreach_reason = Column(String(300), nullable=True)
    # Property attributes from the import (JSON text): beds, baths, price, …
    details = Column(Text, nullable=True)
    score = Column(Integer, nullable=False, default=0)
    # Where this lead came from: 'csv_import' today; a future provider
    # integration (BatchData, PropertyRadar, DealMachine, ...) sets its own
    # value here rather than this being a fixed enum.
    source = Column(String(50), nullable=False, default='csv_import')
    # Set once the owner opts out anywhere; the lead can never be campaigned again.
    dnc = Column(Boolean, nullable=False, default=False)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=True, index=True)
    # The campaign this lead was last added to, draft or sent. A lead already
    # in a sent campaign is blocked from joining another, so in practice this
    # is the one campaign it belongs to.
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=True, index=True)
    # A replied lead may be handed by its broker to one of that broker's agents.
    assigned_agent_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    assignment_stage = Column(String(20), nullable=False, default='new')
    assignment_stage_changed_at = Column(DateTime, nullable=True)
    assignment_stage_seconds = Column(Text, nullable=False, default='{}')
    # When the broker last reached this lead from the SMS workspace.
    last_activity_at = Column(DateTime, nullable=True)
    refreshed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User', foreign_keys=[user_id])
    conversation = relationship('Conversation')
    assigned_agent = relationship('User', foreign_keys=[assigned_agent_id])


@event.listens_for(Lead, 'before_insert')
def _queue_new_lead_for_geocoding(_mapper, _connection, lead):
    from .location.queue import prepare_lead

    prepare_lead(lead)


@event.listens_for(Lead, 'before_update')
def _requeue_changed_lead_address(_mapper, _connection, lead):
    state = inspect(lead)
    if state.attrs.property_address.history.has_changes() or state.attrs.area.history.has_changes():
        from .location.queue import prepare_lead

        prepare_lead(lead)


class LeadEvent(Base):
    """One entry in a lead's history: a stage move, an assignment change, an
    AI/agent ownership handoff, or a notable activity (e.g. a meeting booked).

    Everything the timeline shows is a row here — there is no separate stage
    history table, so a lead visiting "assigned" twice (revoke, reassign)
    just adds two rows rather than overwriting one.
    """
    __tablename__ = 'lead_events'
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey('leads.id'), nullable=False, index=True)
    # 'stage' | 'assignment' | 'ownership' | 'activity' — lets the UI pick an
    # icon/lane without parsing event_type.
    event_category = Column(String(20), nullable=False)
    # e.g. 'attached_to_campaign', 'assigned', 'revoked', 'reassigned',
    # 'stage_changed', 'handover_to_ai', 'handover_to_agent', 'meeting_booked'.
    event_type = Column(String(50), nullable=False)
    # Who caused it: 'system' | 'broker' | 'hob' | 'agent' | 'ai'.
    actor_type = Column(String(20), nullable=False, default='system')
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    # For assignment/reassignment: who the lead went to.
    target_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    from_value = Column(String(100), nullable=True)
    to_value = Column(String(100), nullable=True)
    reason = Column(Text, nullable=True)
    # Free-form JSON text for anything else worth keeping (e.g. campaign name).
    meta = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    lead = relationship('Lead')
    actor = relationship('User', foreign_keys=[actor_id])
    target = relationship('User', foreign_keys=[target_id])


class AiRun(Base):
    __tablename__ = 'ai_runs'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    model = Column(String(255))
    prompt = Column(Text)
    result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)

    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    device_id = Column(
        String(100),
        nullable=False,
        index=True,
    )

    endpoint = Column(
        Text,
        nullable=False,
        unique=True,
    )

    p256dh = Column(Text, nullable=False)
    auth = Column(Text, nullable=False)

    is_active = Column(
        Boolean,
        default=True,
        nullable=False,
    )
class BookingReminder(Base):
    __tablename__ = "booking_reminders"

    id = Column(
        Integer,
        primary_key=True,
    )

    # Keep
    booking_id = Column(
        Integer,
        ForeignKey(
            "bookings.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Keep
    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    reminder_type = Column(
        String(50),
        nullable=False,
    )

    # REMOVE individual index=True
    scheduled_for = Column(
        DateTime,
        nullable=False,
    )

    # REMOVE individual index=True
    status = Column(
        String(20),
        nullable=False,
        default="pending",
    )

    sent_at = Column(
        DateTime,
        nullable=True,
    )

    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
    )

    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    booking = relationship("Booking")
    user = relationship("User")

    # IMPORTANT INDEX FOR YOUR SCHEDULER QUERY
    __table_args__ = (
        Index(
            "ix_booking_reminders_status_scheduled_for",
            "status",
            "scheduled_for",
        ),
    )


class Notification(Base):
    __tablename__ = "notifications"

    # Primary key already indexed.
    id = Column(
        Integer,
        primary_key=True,
    )

    # Keep: useful for getting a user's notifications
    user_id = Column(
        Integer,
        ForeignKey(
            "users.id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )

    # Keep if notifications are looked up by booking
    booking_id = Column(
        Integer,
        ForeignKey(
            "bookings.id",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )

    reminder_id = Column(
        Integer,
        ForeignKey(
            "booking_reminders.id",
            ondelete="SET NULL",
        ),
        nullable=True,
        unique=True,
    )

    type = Column(
        String(50),
        nullable=False,
    )

    title = Column(
        String(255),
        nullable=False,
    )

    message = Column(
        Text,
        nullable=False,
    )

    # Keep for now because you may query unread notifications.
    is_read = Column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    read_at = Column(
        DateTime,
        nullable=True,
    )

    action_url = Column(
        String(500),
        nullable=True,
    )
    conversation_id = Column(
    Integer,
    ForeignKey("conversations.id"),
    nullable=True,
    )

    unread_count = Column(
    Integer,
    nullable=False,
    default=1,
     )

    # Keep if notification history is ordered by created time.
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    user = relationship("User")
    booking = relationship("Booking")
    reminder = relationship("BookingReminder")
