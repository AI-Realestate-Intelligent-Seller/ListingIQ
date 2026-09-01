from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
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
    # Agents are assigned to one Area Broker by the HOB who invites them.
    assigned_broker_id = Column(Integer, ForeignKey('users.id'), nullable=True, index=True)
    google_refresh_token = Column(Text, nullable=True)  # encrypted
    created_at = Column(DateTime, default=datetime.utcnow)
    assigned_broker = relationship('User', remote_side=[id], foreign_keys=[assigned_broker_id])

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
    conversation_id = Column(Integer, ForeignKey('conversations.id'))
    direction = Column(String(20))
    from_number = Column(String(50))
    to_number = Column(String(50))
    text = Column(Text)
    status = Column(String(50))
    event_type = Column(String(100))
    # The campaign that sent this message, for outreach sent by one. A thread
    # can carry outreach from more than one campaign when an owner has more
    # than one property, so delivery is attributed per message, not per thread.
    campaign_id = Column(Integer, ForeignKey('campaigns.id'), nullable=True, index=True)
    # Normalised address this outreach was about (see app.leads.address). The
    # thread's own property_key only holds the latest one, so the claim on a
    # property has to live on the message: messages are history, a conversation
    # is a moving present.
    property_key = Column(String(500), nullable=True, index=True)
    telnyx_id = Column(String(128), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    conversation = relationship('Conversation')

class Booking(Base):
    __tablename__ = 'bookings'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), index=True)
    phone = Column(String(50))
    name = Column(String(255), nullable=True)
    title = Column(String(255))
    start_at = Column(DateTime)
    end_at = Column(DateTime)
    join_token = Column(String(128))
    created_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    """A named batch of outreach: one message template sent to many leads.

    A campaign is drafted before it is sent. `create campaign` on a selection
    in the Lead Pool creates a draft holding those leads, and the broker names
    it and writes the opening message in the Campaigns tab. Sending renders the
    template once per lead and hands each one to Bobbie.

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
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    owner_name = Column(String(255))
    phone = Column(String(50), index=True)
    property_address = Column(String(500))
    area = Column(String(120))
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
