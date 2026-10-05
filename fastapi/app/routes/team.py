"""Team management: brokerage invitations issued by a Head of Brokerage (HOB).

Flow: an authenticated HOB invites an email address as `broker` or `agent`.
The invitee receives a one-time link, opens `/join?token=...`, and completes
registration. The role and brokerage always come from the stored invitation —
never from the request body.
"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..auth import ACCESS_TOKEN_EXPIRE_MINUTES, create_access_token, hash_password
from ..core.email import EmailDeliveryError, build_invitation_url, send_invitation_email
from ..logger import get_logger
from ..models import Booking, Campaign, Conversation, Invitation, Lead, Message, User
from ..schemas import (
    INVITABLE_ROLES,
    InvitationAcceptRequest,
    InvitationAcceptResponse,
    InvitationCreate,
    InvitationResponse,
    InvitationValidationResponse,
    TeamDirectoryResponse,
)
from ..tenancy import brokerage_user_ids
from .auth import get_current_user, get_db

router = APIRouter()
logger = get_logger(__name__)

INVITATION_TTL_HOURS = 48

ROLE_LABELS = {
    'hob': 'the Head of Brokerage',
    'broker': 'an Area Broker',
    'agent': 'an Agent',
}
ROLE_DISPLAY_NAMES = {
    'hob': 'Head of Brokerage',
    'broker': 'Area Broker',
    'agent': 'Agent',
}

INVALID_INVITATION_MESSAGE = 'This invitation is invalid or has expired.'


@router.get('/overview')
def brokerage_overview(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Brokerage-wide operating metrics for the HOB Overview screen."""
    if current_user.role != 'hob':
        raise HTTPException(status_code=403, detail='Only a Head of Brokerage can view brokerage analytics.')

    user_ids = brokerage_user_ids(session, current_user)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    leads = session.query(Lead).filter(Lead.user_id.in_(user_ids)).all()
    campaigns_sent = (session.query(Campaign)
                      .filter(Campaign.user_id.in_(user_ids),
                              Campaign.status == 'sent',
                              Campaign.sent_at >= month_start)
                      .count())

    outreach = (session.query(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .filter(Conversation.user_id.in_(user_ids),
                        Message.direction == 'outbound',
                        Message.campaign_id.isnot(None))
                .all())
    outreach_conversation_ids = {message.conversation_id for message in outreach}
    replied_ids = set()
    if outreach_conversation_ids:
        replied_ids = {row[0] for row in session.query(Message.conversation_id)
                       .filter(Message.conversation_id.in_(outreach_conversation_ids),
                               Message.direction == 'inbound').distinct().all()}

    conversations = (session.query(Conversation)
                     .filter(Conversation.user_id.in_(user_ids)).all())
    conversation_ids = [conversation.id for conversation in conversations]
    messages = ([] if not conversation_ids else
                session.query(Message)
                .filter(Message.conversation_id.in_(conversation_ids))
                .order_by(Message.conversation_id, Message.created_at, Message.id).all())
    grouped: dict[int, list[Message]] = {}
    for message in messages:
        grouped.setdefault(message.conversation_id, []).append(message)

    requiring_attention = sum(
        1 for conversation in conversations
        if grouped.get(conversation.id)
        and grouped[conversation.id][-1].direction == 'inbound'
        and conversation.handled_by == 'broker'
    )
    response_seconds = []
    for rows in grouped.values():
        for index, message in enumerate(rows):
            if message.direction != 'inbound' or not message.created_at:
                continue
            response = next((row for row in rows[index + 1:]
                             if row.direction == 'outbound' and row.created_at), None)
            if response:
                response_seconds.append(max((response.created_at - message.created_at).total_seconds(), 0))

    bookings = (session.query(Booking)
                .filter(Booking.user_id.in_(user_ids)).all())
    bookings_this_month = sum(1 for booking in bookings
                              if booking.created_at and booking.created_at >= month_start)
    lead_phones = {lead.phone for lead in leads if lead.phone}
    booked_lead_phones = {booking.phone for booking in bookings if booking.phone in lead_phones}

    members = (session.query(User)
               .filter(User.id.in_(user_ids), User.role.in_(('broker', 'agent')), User.is_active.is_(True))
               .order_by(User.role, User.full_name).all())
    workload = []
    for member in members:
        count = ((session.query(Lead).filter(Lead.assigned_agent_id == member.id).count())
                 if member.role == 'agent' else
                 session.query(Lead).filter(Lead.user_id == member.id).count())
        workload.append({'user_id': member.id, 'name': member.full_name or member.email,
                         'role': member.role, 'lead_count': count})

    total_outreach = len(outreach_conversation_ids)
    return {
        'total_leads': len(leads),
        'campaigns_sent_this_month': campaigns_sent,
        'campaign_conversations': total_outreach,
        'campaign_replied': len(replied_ids),
        'campaign_reply_rate': round((len(replied_ids) / total_outreach * 100) if total_outreach else 0, 1),
        'replies_requiring_attention': requiring_attention,
        'appointments_booked_this_month': bookings_this_month,
        'booked_leads': len(booked_lead_phones),
        'lead_to_appointment_rate': round((len(booked_lead_phones) / len(lead_phones) * 100) if lead_phones else 0, 1),
        'average_response_seconds': round(sum(response_seconds) / len(response_seconds)) if response_seconds else None,
        'workload': workload,
    }


def hash_token(token: str) -> str:
    """Only the hash is persisted; the raw token lives solely in the invite URL."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def email_domain(email: str) -> str:
    _, _, domain = (email or '').partition('@')
    return domain.strip().lower()


def get_brokerage_domain(user: User) -> str:
    """The brokerage's email domain.

    There is no separate Brokerage table in this project — a brokerage is the
    set of users sharing a `brokerage_id`, and its domain is the email domain of
    the Head of Brokerage who registered it. The dashboard derives the domain the
    same way, so both sides agree.
    """
    return email_domain(user.email)


def get_invitation_by_token(session: Session, token: str) -> Optional[Invitation]:
    if not token:
        return None
    return (
        session.query(Invitation)
        .filter(Invitation.token_hash == hash_token(token))
        .first()
    )


def is_claimable(invitation: Optional[Invitation]) -> bool:
    return bool(
        invitation
        and invitation.status == 'pending'
        and invitation.expires_at
        and invitation.expires_at > datetime.utcnow()
    )


def expire_if_stale(session: Session, invitation: Invitation) -> None:
    """Flip a lapsed pending invitation to `expired` so its state stays truthful."""
    if (
        invitation.status == 'pending'
        and invitation.expires_at
        and invitation.expires_at <= datetime.utcnow()
    ):
        invitation.status = 'expired'
        session.commit()


@router.post('/invitations', response_model=InvitationResponse)
def create_invitation(
    invitation_in: InvitationCreate,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    # Authorization: only a Head of Brokerage may invite, and only into their own
    # brokerage. Nothing about the brokerage is read from the request body.
    if current_user.role != 'hob':
        raise HTTPException(status_code=403, detail='Only a Head of Brokerage can invite team members.')

    role = invitation_in.role  # already normalized/validated to broker|agent
    if role not in INVITABLE_ROLES:
        raise HTTPException(status_code=400, detail='You can only invite an Area Broker or an Agent.')

    if not current_user.brokerage_id:
        raise HTTPException(status_code=400, detail='Your brokerage information is incomplete. Please contact support.')

    assigned_broker = None
    if role == 'agent':
        if invitation_in.broker_id is None:
            raise HTTPException(status_code=400, detail='Select an Area Broker for this agent.')
        assigned_broker = (
            session.query(User)
            .filter(
                User.id == invitation_in.broker_id,
                User.role == 'broker',
                User.brokerage_id == current_user.brokerage_id,
                User.is_active.is_(True),
            )
            .first()
        )
        if not assigned_broker:
            raise HTTPException(status_code=400, detail='Select an active Area Broker from your brokerage.')

    email = str(invitation_in.email).strip().lower()
    brokerage_domain = get_brokerage_domain(current_user)
    if not brokerage_domain:
        raise HTTPException(status_code=400, detail='Your brokerage information is incomplete. Please contact support.')

    if email_domain(email) != brokerage_domain:
        raise HTTPException(
            status_code=400,
            detail=f'Team members must use your brokerage domain (@{brokerage_domain}).',
        )

    if email == current_user.email.lower():
        raise HTTPException(status_code=400, detail='You cannot invite yourself.')

    existing_user = session.query(User).filter(User.email == email).first()
    if existing_user:
        if existing_user.brokerage_id == current_user.brokerage_id:
            raise HTTPException(status_code=400, detail='This user is already a member of your brokerage.')
        raise HTTPException(
            status_code=400,
            detail='This email address is already registered to another brokerage.',
        )

    now = datetime.utcnow()
    pending = (
        session.query(Invitation)
        .filter(
            Invitation.email == email,
            Invitation.brokerage_id == current_user.brokerage_id,
            Invitation.status == 'pending',
            Invitation.expires_at > now,
        )
        .order_by(Invitation.created_at.desc())
        .first()
    )

    # Invitation resending is intentionally unsupported. The original link
    # remains the only valid link until it is accepted or expires.
    if pending:
        raise HTTPException(
            status_code=409,
            detail='An active invitation already exists for this email address.',
        )

    token = generate_token()
    expires_at = now + timedelta(hours=INVITATION_TTL_HOURS)
    invitation = Invitation(
        email=email,
        role=role,
        brokerage_id=current_user.brokerage_id,
        brokerage_name=current_user.brokerage_name,
        invited_by=current_user.id,
        assigned_broker_id=assigned_broker.id if assigned_broker else None,
        token_hash=hash_token(token),
        expires_at=expires_at,
        status='pending',
        created_at=now,
    )
    session.add(invitation)
    session.flush()

    try:
        send_invitation_email(
            recipient_email=email,
            brokerage_name=current_user.brokerage_name or 'Your brokerage',
            role=ROLE_LABELS[role],
            invitation_url=build_invitation_url(token),
            expires_at=expires_at.strftime('%B %d, %Y at %I:%M %p UTC'),
        )
    except EmailDeliveryError:
        # Nothing is persisted if the invitation could not be delivered.
        session.rollback()
        raise HTTPException(
            status_code=502,
            detail='We could not send the invitation email. Please try again in a moment.',
        )
    except Exception:
        session.rollback()
        logger.exception('invitation_email_unexpected_failure')
        raise HTTPException(
            status_code=502,
            detail='We could not send the invitation email. Please try again in a moment.',
        )

    session.commit()
    logger.info('invitation_created role=%s brokerage_id=%s', role, current_user.brokerage_id)
    return {'message': 'Invitation sent successfully.'}


@router.get('/directory', response_model=TeamDirectoryResponse)
def get_team_directory(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Return active brokers and agents in the HOB's own brokerage."""
    if current_user.role != 'hob':
        raise HTTPException(status_code=403, detail='Only a Head of Brokerage can view the team directory.')
    if not current_user.brokerage_id:
        raise HTTPException(status_code=400, detail='Your brokerage information is incomplete. Please contact support.')

    members = (
        session.query(User)
        .filter(
            User.brokerage_id == current_user.brokerage_id,
            User.role.in_(INVITABLE_ROLES),
            User.is_active.is_(True),
        )
        .order_by(User.role.asc(), User.full_name.asc(), User.email.asc())
        .all()
    )
    return {'members': members}


@router.get('/invitations/{token}', response_model=InvitationValidationResponse)
def validate_invitation(token: str, session: Session = Depends(get_db)):
    """Public: the join page calls this before rendering the registration form."""
    invitation = get_invitation_by_token(session, token)
    if invitation:
        expire_if_stale(session, invitation)

    if not is_claimable(invitation):
        return {'valid': False, 'message': INVALID_INVITATION_MESSAGE}

    return {
        'valid': True,
        'email': invitation.email,
        'role': invitation.role,
        'role_label': ROLE_DISPLAY_NAMES.get(invitation.role, invitation.role),
        'brokerage_name': invitation.brokerage_name,
        'expires_at': invitation.expires_at,
    }


@router.post('/invitations/{token}/accept', response_model=InvitationAcceptResponse)
def accept_invitation(
    token: str,
    payload: InvitationAcceptRequest,
    session: Session = Depends(get_db),
):
    invitation = get_invitation_by_token(session, token)
    if invitation:
        expire_if_stale(session, invitation)

    if not is_claimable(invitation):
        raise HTTPException(status_code=400, detail=INVALID_INVITATION_MESSAGE)

    email = (invitation.email or '').strip().lower()
    if session.query(User).filter(User.email == email).first():
        raise HTTPException(
            status_code=400,
            detail='An account already exists for this email address. Please log in instead.',
        )

    # Claim the invitation atomically: the conditional UPDATE only matches while
    # the row is still pending, so a concurrent request cannot reuse the token.
    accepted_at = datetime.utcnow()
    claimed = (
        session.query(Invitation)
        .filter(Invitation.id == invitation.id, Invitation.status == 'pending')
        .update(
            {'status': 'accepted', 'accepted_at': accepted_at},
            synchronize_session=False,
        )
    )
    if claimed != 1:
        session.rollback()
        raise HTTPException(status_code=400, detail=INVALID_INVITATION_MESSAGE)

    first_name = payload.first_name.strip()
    last_name = payload.last_name.strip()
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=f'{first_name} {last_name}',
        first_name=first_name,
        last_name=last_name,
        # Role and brokerage are taken from the invitation, never from the request.
        brokerage_name=invitation.brokerage_name,
        brokerage_id=invitation.brokerage_id,
        role=invitation.role,
        assigned_broker_id=invitation.assigned_broker_id,
        is_head_or_owner=invitation.role == 'hob',
        # Delivering the invitation to this mailbox already proved ownership.
        is_verified=True,
        is_active=True,
    )
    session.add(user)

    try:
        session.commit()
    except Exception:
        session.rollback()
        logger.exception('invitation_accept_failed')
        raise HTTPException(status_code=400, detail='We could not complete your registration. Please try again.')

    session.refresh(user)

    access_token = create_access_token({'user_id': user.id, 'role': user.role})
    refresh_token = create_access_token(
        {'user_id': user.id, 'role': user.role},
        expires_delta=timedelta(days=30),
    )
    return {
        'message': (
            f'Account created successfully. You are now a member of '
            f'{user.brokerage_name} as {ROLE_LABELS.get(user.role, user.role)}.'
        ),
        'access_token': access_token,
        'refresh_token': refresh_token,
        'token_type': 'bearer',
        'expires_in': ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        'user': user,
    }
