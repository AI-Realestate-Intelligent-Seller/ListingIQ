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
    google_refresh_token = Column(Text, nullable=True)  # encrypted
    created_at = Column(DateTime, default=datetime.utcnow)

class Invitation(Base):
    __tablename__ = 'invitations'
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    role = Column(String(50), nullable=False)
    brokerage_id = Column(String(255), nullable=False, index=True)
    brokerage_name = Column(String(255))
    invited_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    status = Column(String(50), nullable=False, default='pending')
    accepted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    invited_by_user = relationship('User', foreign_keys=[invited_by])

class Conversation(Base):
    __tablename__ = 'conversations'
    id = Column(Integer, primary_key=True, index=True)
    contact = Column(String(50), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'))
    name = Column(String(255))
    property_address = Column(String(500))
    # Bobbie autopilot. On by default for new outreach.
    ai_enabled = Column(Boolean, nullable=False, default=True)
    # Retained for existing rows; the simulated recipient has been removed.
    recipient_ai_enabled = Column(Boolean, nullable=False, default=False)
    # Who owns the next reply: 'bobbie' while she is driving, 'broker' once she
    # has stopped. A broker-owned thread never triggers an automatic reply.
    handled_by = Column(String(20), nullable=False, default='bobbie')
    # Approved lead facts Bobbie is allowed to ground her replies in (JSON text).
    lead_context = Column(Text, nullable=True)
    lead_status = Column(String(50), nullable=False, default='processing')
    queue_status = Column(String(50), nullable=False, default='idle')
    dnc_alert = Column(Boolean, nullable=False, default=False)
    meeting_booked = Column(Boolean, nullable=False, default=False)
    # The Follow-ups decision on a lead who replied: 'pending' until the
    # assignee accepts or declines it.
    followup_state = Column(String(20), nullable=False, default='pending')
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
    # Set once the owner opts out anywhere; the lead can never be campaigned again.
    dnc = Column(Boolean, nullable=False, default=False)
    conversation_id = Column(Integer, ForeignKey('conversations.id'), nullable=True, index=True)
    # When the broker last reached this lead from the SMS workspace.
    last_activity_at = Column(DateTime, nullable=True)
    refreshed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user = relationship('User')
    conversation = relationship('Conversation')


class AiRun(Base):
    __tablename__ = 'ai_runs'
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'))
    model = Column(String(255))
    prompt = Column(Text)
    result = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
