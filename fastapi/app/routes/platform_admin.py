"""Internal company administration, isolated from brokerage administration."""

import json
import os
import platform
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, EmailStr, Field, validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import (
    AiRun,
    Campaign,
    Conversation,
    FeatureFlag,
    Invitation,
    Lead,
    Message,
    PlatformAuditLog,
    User,
)
from . import team
from .auth import get_current_user, get_db

router = APIRouter()


def require_platform_admin(current: User = Depends(get_current_user)) -> User:
    if current.role != 'platform_admin' or not current.is_active:
        raise HTTPException(status_code=403, detail='Platform administrator access required.')
    return current


def audit(session: Session, actor: User, action: str, target_type: str,
          target_id=None, detail=None) -> None:
    session.add(PlatformAuditLog(
        actor_id=actor.id, action=action, target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        detail=json.dumps(detail or {}, separators=(',', ':')),
    ))


def organization_rows(session: Session):
    users = (session.query(User)
             .filter(User.brokerage_id.isnot(None), User.role != 'platform_admin')
             .order_by(User.created_at.asc()).all())
    grouped = {}
    for user in users:
        item = grouped.setdefault(user.brokerage_id, {
            'id': user.brokerage_id,
            'name': user.brokerage_name or 'Unnamed organization',
            'owner_email': None,
            'user_count': 0,
            'active_user_count': 0,
            'created_at': user.created_at,
            'is_active': False,
            'onboarding_status': 'onboarded',
            'invitation_role': None,
        })
        item['user_count'] += 1
        item['active_user_count'] += int(bool(user.is_active))
        item['is_active'] = item['is_active'] or bool(user.is_active)
        if user.role == 'hob':
            item['owner_email'] = user.email
            item['name'] = user.brokerage_name or item['name']
    now = datetime.utcnow()
    pending_invitations = (session.query(Invitation)
                           .filter(Invitation.status == 'pending',
                                   Invitation.expires_at > now)
                           .order_by(Invitation.created_at.asc()).all())
    for invitation in pending_invitations:
        grouped.setdefault(invitation.brokerage_id, {
            'id': invitation.brokerage_id,
            'name': invitation.brokerage_name or 'Unnamed organization',
            'owner_email': invitation.email,
            'user_count': 0,
            'active_user_count': 0,
            'created_at': invitation.created_at,
            'is_active': False,
            'onboarding_status': 'invited',
            'invitation_role': invitation.role,
        })
    return list(grouped.values())


@router.get('/overview')
def overview(_: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    month = datetime.utcnow() - timedelta(days=30)
    organizations = organization_rows(session)
    return {
        'organizations': len(organizations),
        'active_organizations': sum(1 for item in organizations if item['is_active']),
        'users': session.query(User).filter(User.role != 'platform_admin').count(),
        'active_users': session.query(User).filter(User.role != 'platform_admin', User.is_active.is_(True)).count(),
        'leads': session.query(Lead).count(),
        'campaigns': session.query(Campaign).count(),
        'messages': session.query(Message).count(),
        'new_users_30d': session.query(User).filter(User.role != 'platform_admin', User.created_at >= month).count(),
        'pending_invitations': session.query(Invitation).filter(Invitation.status == 'pending').count(),
    }


@router.get('/organizations')
def organizations(_: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    rows = organization_rows(session)
    lead_counts = dict(session.query(User.brokerage_id, func.count(Lead.id))
                       .join(Lead, Lead.user_id == User.id)
                       .filter(User.brokerage_id.isnot(None)).group_by(User.brokerage_id).all())
    for row in rows:
        row['lead_count'] = lead_counts.get(row['id'], 0)
    return {'organizations': sorted(rows, key=lambda item: item['name'].lower())}


class BrokerageOnboardingCreate(BaseModel):
    brokerage_name: str = Field(min_length=2, max_length=255)
    invite_email: EmailStr
    initial_role: str = Field(regex=r'^(agent|hob|broker)$')

    @validator('brokerage_name')
    def clean_brokerage_name(cls, value):
        clean = value.strip()
        if len(clean) < 2:
            raise ValueError('Brokerage name is required.')
        return clean


@router.post('/organizations', status_code=201)
def create_organization(
    payload: BrokerageOnboardingCreate,
    current: User = Depends(require_platform_admin),
    session: Session = Depends(get_db),
):
    """Create a brokerage identity and send its first user a one-time onboarding link."""
    email = str(payload.invite_email).strip().lower()
    name = payload.brokerage_name.strip()
    now = datetime.utcnow()
    if session.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail='This email is already registered.')
    if (session.query(Invitation)
            .filter(func.lower(Invitation.email) == email,
                    Invitation.status == 'pending',
                    Invitation.expires_at > now).first()):
        raise HTTPException(status_code=409, detail='An active invitation already exists for this email.')
    if (session.query(User)
            .filter(func.lower(User.brokerage_name) == name.lower()).first()
            or session.query(Invitation)
            .filter(func.lower(Invitation.brokerage_name) == name.lower(),
                    Invitation.status == 'pending',
                    Invitation.expires_at > now).first()):
        raise HTTPException(status_code=409, detail='A brokerage with this name already exists.')

    brokerage_id = str(uuid.uuid4())
    token = team.generate_token()
    expires_at = now + timedelta(hours=team.INVITATION_TTL_HOURS)
    invitation = Invitation(
        email=email,
        role=payload.initial_role,
        brokerage_id=brokerage_id,
        brokerage_name=name,
        invited_by=current.id,
        assigned_broker_id=None,
        token_hash=team.hash_token(token),
        expires_at=expires_at,
        status='pending',
        created_at=now,
    )
    session.add(invitation)
    session.flush()
    try:
        team.send_invitation_email(
            recipient_email=email,
            brokerage_name=name,
            role=team.ROLE_LABELS[payload.initial_role],
            invitation_url=team.build_invitation_url(token),
            expires_at=expires_at.strftime('%B %d, %Y at %I:%M %p UTC'),
        )
    except team.EmailDeliveryError:
        session.rollback()
        raise HTTPException(
            status_code=502,
            detail='We could not send the onboarding email. Please try again.',
        ) from None
    except Exception:
        session.rollback()
        team.logger.exception('brokerage_onboarding_email_unexpected_failure')
        raise HTTPException(
            status_code=502,
            detail='We could not send the onboarding email. Please try again.',
        ) from None

    audit(session, current, 'organization.onboarding_invited', 'organization',
          brokerage_id, {
              'brokerage_name': name,
              'invite_email': email,
              'initial_role': payload.initial_role,
          })
    session.commit()
    return {
        'message': 'Brokerage created and onboarding invitation sent.',
        'brokerage_id': brokerage_id,
        'expires_at': expires_at,
    }


class ActiveUpdate(BaseModel):
    is_active: bool
    reason: str = Field(min_length=3, max_length=300)


class ScreenAccessUpdate(BaseModel):
    role: str = Field(regex=r'^(agent|hob|broker)$')


@router.patch('/organizations/{brokerage_id}')
def update_organization(brokerage_id: str, payload: ActiveUpdate,
                        current: User = Depends(require_platform_admin),
                        session: Session = Depends(get_db)):
    users = session.query(User).filter(User.brokerage_id == brokerage_id, User.role != 'platform_admin').all()
    if not users:
        raise HTTPException(status_code=404, detail='Organization not found.')
    for user in users:
        user.is_active = payload.is_active
    audit(session, current, 'organization.activated' if payload.is_active else 'organization.suspended',
          'organization', brokerage_id, {'reason': payload.reason, 'affected_users': len(users)})
    session.commit()
    return {'message': 'Organization activated.' if payload.is_active else 'Organization suspended.'}


@router.get('/users')
def users(q: str = Query('', max_length=100), _: User = Depends(require_platform_admin),
          session: Session = Depends(get_db)):
    query = session.query(User).filter(User.role != 'platform_admin')
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(User.email.ilike(term), User.full_name.ilike(term),
                                 User.brokerage_name.ilike(term)))
    rows = query.order_by(User.created_at.desc()).limit(250).all()
    return {'users': [{
        'id': user.id, 'email': user.email, 'full_name': user.full_name,
        'role': user.role, 'brokerage_id': user.brokerage_id,
        'brokerage_name': user.brokerage_name, 'is_active': user.is_active,
        'is_verified': user.is_verified, 'created_at': user.created_at,
    } for user in rows]}


@router.patch('/users/{user_id}')
def update_user(user_id: int, payload: ActiveUpdate,
                current: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    user = session.query(User).filter(User.id == user_id, User.role != 'platform_admin').first()
    if not user:
        raise HTTPException(status_code=404, detail='Customer user not found.')
    user.is_active = payload.is_active
    audit(session, current, 'user.activated' if payload.is_active else 'user.suspended',
          'user', user.id, {'reason': payload.reason, 'email': user.email})
    session.commit()
    return {'message': 'User activated.' if payload.is_active else 'User suspended.'}


@router.patch('/users/{user_id}/screen-access')
def update_user_screen_access(
    user_id: int,
    payload: ScreenAccessUpdate,
    current: User = Depends(require_platform_admin),
    session: Session = Depends(get_db),
):
    user = session.query(User).filter(User.id == user_id, User.role != 'platform_admin').first()
    if not user:
        raise HTTPException(status_code=404, detail='Customer user not found.')

    previous_role = user.role
    user.role = payload.role
    user.is_head_or_owner = payload.role == 'hob'
    if payload.role != 'agent':
        user.assigned_broker_id = None

    audit(
        session,
        current,
        'user.screen_access_updated',
        'user',
        user.id,
        {
            'email': user.email,
            'previous_role': previous_role,
            'role': payload.role,
        },
    )
    session.commit()
    return {
        'message': 'Screen access updated. The user must sign in again to open the new screen.',
        'role': user.role,
    }


class FlagUpdate(BaseModel):
    key: str = Field(min_length=2, max_length=100, regex=r'^[a-z0-9_.-]+$')
    description: str = Field('', max_length=500)
    enabled: bool = False
    brokerage_id: str | None = None


@router.get('/feature-flags')
def flags(_: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    rows = session.query(FeatureFlag).order_by(FeatureFlag.key, FeatureFlag.brokerage_id).all()
    return {'flags': rows}


@router.post('/feature-flags')
def save_flag(payload: FlagUpdate, current: User = Depends(require_platform_admin),
              session: Session = Depends(get_db)):
    brokerage_id = payload.brokerage_id or None
    flag = session.query(FeatureFlag).filter(
        FeatureFlag.key == payload.key, FeatureFlag.brokerage_id == brokerage_id).first()
    if not flag:
        flag = FeatureFlag(key=payload.key, brokerage_id=brokerage_id)
        session.add(flag)
    flag.description = payload.description.strip()
    flag.enabled = payload.enabled
    flag.updated_by = current.id
    flag.updated_at = datetime.utcnow()
    audit(session, current, 'feature_flag.updated', 'feature_flag', payload.key,
          {'enabled': payload.enabled, 'brokerage_id': brokerage_id})
    session.commit()
    session.refresh(flag)
    return flag


@router.get('/operations')
def operations(_: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    now = datetime.utcnow()
    return {
        'pending_invitations': session.query(Invitation).filter(Invitation.status == 'pending').count(),
        'draft_campaigns': session.query(Campaign).filter(Campaign.status == 'draft').count(),
        'queued_conversations': session.query(Conversation).filter(Conversation.queue_status != 'idle').count(),
        'due_followups': session.query(Conversation).filter(Conversation.next_followup_at <= now).count(),
        'failed_messages': session.query(Message).filter(Message.status.in_(('failed', 'undelivered'))).count(),
        'ai_runs_24h': session.query(AiRun).filter(AiRun.created_at >= now - timedelta(days=1)).count(),
    }


@router.get('/audit-log')
def audit_log(_: User = Depends(require_platform_admin), session: Session = Depends(get_db)):
    rows = session.query(PlatformAuditLog).order_by(PlatformAuditLog.created_at.desc()).limit(250).all()
    actors = {u.id: u.email for u in session.query(User).filter(User.id.in_({r.actor_id for r in rows if r.actor_id})).all()}
    return {'events': [{
        'id': row.id, 'actor_email': actors.get(row.actor_id, 'System'), 'action': row.action,
        'target_type': row.target_type, 'target_id': row.target_id,
        'detail': row.detail, 'created_at': row.created_at,
    } for row in rows]}


@router.get('/engineering')
def engineering(_: User = Depends(require_platform_admin)):
    return {
        'environment': os.getenv('APP_ENV', 'development'),
        'release': os.getenv('APP_VERSION', 'local'),
        'commit': os.getenv('GIT_COMMIT', 'unknown'),
        'python': platform.python_version(),
        'database': 'sqlite' if os.getenv('POSTGRES_URL', '').startswith('sqlite') else 'postgresql',
        'sms_mode': os.getenv('SMS_MODE', 'simulation'),
    }
