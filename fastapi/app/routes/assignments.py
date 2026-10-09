"""Broker assignment queue and agent workflow for replied leads."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.email import EmailDeliveryError, send_lead_assigned_email
from ..db import SessionLocal
from ..leads import events as lead_events
from ..leads.response_analytics import get_agent_response_analytics
from ..leads.service import serialize
from ..models import Booking, Campaign, Conversation, Lead, Message, User
from .. import query_progress
from ..reminder.notification import notify_user
from ..schemas import LeadAssignmentRequest, LeadAssignmentStageRequest
from .auth import get_current_user, get_db

router = APIRouter()


def _email_assignment(agent: User, broker: User, lead: Lead, reassigned: bool = False) -> None:
    if not agent.is_active or not agent.is_verified:
        return
    try:
        send_lead_assigned_email(
            recipient_email=agent.email,
            broker_name=broker.full_name or broker.email,
            lead_id=lead.id,
            lead_name=lead.owner_name,
            property_address=lead.property_address,
            reassigned=reassigned,
        )
    except EmailDeliveryError as error:
        print('[LEAD ASSIGNMENT EMAIL FAILED]', f'lead_id={lead.id}', repr(error))
    except Exception as error:  # noqa: BLE001 - email must not undo an assignment
        print('[LEAD ASSIGNMENT EMAIL UNEXPECTED FAILURE]', f'lead_id={lead.id}', repr(error))


def _require_broker(user: User) -> None:
    if user.role != 'broker':
        raise HTTPException(status_code=403, detail='Only an Area Broker can assign replied leads.')


def _require_agent(user: User) -> None:
    if user.role != 'agent':
        raise HTTPException(status_code=403, detail='Only an Agent can view assigned leads here.')


def _linked_agents(session: Session, broker: User) -> list[User]:
    return (
        session.query(User)
        .filter(
            User.role == 'agent',
            User.assigned_broker_id == broker.id,
            User.brokerage_id == broker.brokerage_id,
            User.is_active.is_(True),
        )
        .order_by(User.full_name.asc(), User.email.asc())
        .all()
    )


def _replied_leads(session: Session, broker: User) -> list[Lead]:
    return (
        session.query(Lead)
        .join(Conversation, Conversation.id == Lead.conversation_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(
            Conversation.user_id == broker.id,
            Lead.user_id == broker.id,
            Message.direction == 'inbound',
        )
        .distinct()
        .order_by(Lead.last_activity_at.desc(), Lead.id.desc())
        .all()
    )


ASSIGNMENT_STAGES = ('new', 'processing', 'want_more_info', 'interested', 'ready_to_sell',
                     'location_discussion', 'not_interested', 'no_response', 'dnc')
IN_PROGRESS_STAGES = {'processing', 'want_more_info', 'interested', 'location_discussion'}
COMPLETED_STAGES = {'ready_to_sell', 'not_interested', 'no_response', 'dnc'}


def _assignment_stage(lead: Lead) -> str:
    """Normalize the old three-stage workflow while existing rows transition."""
    return {'in_progress': 'processing', 'done': 'ready_to_sell'}.get(
        lead.assignment_stage or 'new', lead.assignment_stage or 'new')


def _stage_seconds(lead: Lead, now: datetime | None = None) -> dict[str, int]:
    try:
        stored = json.loads(lead.assignment_stage_seconds or '{}')
    except (TypeError, ValueError):
        stored = {}
    totals = {stage: max(int(stored.get(stage, 0)), 0) for stage in ASSIGNMENT_STAGES}
    stage = _assignment_stage(lead)
    if lead.assigned_agent_id and lead.assignment_stage_changed_at and stage in totals:
        elapsed = max(int(((now or datetime.utcnow()) - lead.assignment_stage_changed_at).total_seconds()), 0)
        totals[stage] += elapsed
    return totals


def _serialize_assignment(session: Session, lead: Lead,
                          campaign_names: dict[int, str] | None = None) -> dict:
    item = serialize(lead)
    item['assignee_id'] = lead.assigned_agent_id
    item['assignment_stage'] = _assignment_stage(lead)
    item['assignment_stage_changed_at'] = lead.assignment_stage_changed_at
    item['stage_seconds'] = _stage_seconds(lead)
    item['campaign_id'] = lead.campaign_id
    if campaign_names is None:
        campaign = session.get(Campaign, lead.campaign_id) if lead.campaign_id else None
        name = campaign.name if campaign else None
    else:
        name = campaign_names.get(lead.campaign_id)
    item['campaign_name'] = name or 'Uncategorized'
    return item


def _assignment_payload(session: Session, current_user: User, progress=None) -> dict:
    agents = _linked_agents(session, current_user)
    leads = _replied_leads(session, current_user)
    total = len(leads)
    if progress:
        progress(total, 0)
    # One lookup for every campaign name; a lead may point at a deleted campaign.
    campaign_ids = {lead.campaign_id for lead in leads if lead.campaign_id}
    campaign_names = dict(session.query(Campaign.id, Campaign.name)
                          .filter(Campaign.id.in_(campaign_ids)).all()) if campaign_ids else {}
    serialized = []
    for loaded, lead in enumerate(leads, start=1):
        serialized.append(_serialize_assignment(session, lead, campaign_names))
        if progress:
            progress(total, loaded)
    return {
        'leads': serialized,
        'agents': [
            {'id': agent.id, 'full_name': agent.full_name, 'email': agent.email}
            for agent in agents
        ],
    }


@router.get('')
def list_assignments(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_broker(current_user)
    return _assignment_payload(session, current_user)


def _load_assignments_job(job_id: str, user_id: int) -> None:
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError('The account no longer exists.')
        _require_broker(user)
        payload = _assignment_payload(
            session,
            user,
            progress=lambda total, loaded: query_progress.update(
                job_id, total=total, loaded=loaded,
            ),
        )
        query_progress.complete(job_id, payload, total=len(payload['leads']))
    except Exception as error:  # noqa: BLE001 - the job must report every failure
        query_progress.fail(job_id, error)
    finally:
        session.close()


@router.post('/load', status_code=202)
def start_assignments_load(
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Start a counted broker-assignment query backed by Redis progress."""
    _require_broker(current_user)
    try:
        job = query_progress.create(current_user.id, 'assignments')
    except query_progress.ProgressStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    background.add_task(_load_assignments_job, job['job_id'], current_user.id)
    return {'job_id': job['job_id']}


@router.get('/overview')
def broker_overview(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Campaign, assignment, and booking metrics for an Area Broker."""
    _require_broker(current_user)
    return _broker_overview_payload(session, current_user)


def _broker_overview_payload(session: Session, current_user: User, progress=None) -> dict:
    total = 5
    campaigns = session.query(Campaign).filter(Campaign.user_id == current_user.id).all()
    if progress:
        progress(total, 1)
    sent_campaign_ids = [campaign.id for campaign in campaigns if campaign.status == 'sent']
    campaign_leads = ([] if not sent_campaign_ids else
                      session.query(Lead).filter(
                          Lead.user_id == current_user.id,
                          Lead.campaign_id.in_(sent_campaign_ids),
                      ).all())
    if progress:
        progress(total, 2)
    conversation_ids = [lead.conversation_id for lead in campaign_leads if lead.conversation_id]
    conversations = ([] if not conversation_ids else
                     session.query(Conversation).filter(Conversation.id.in_(conversation_ids)).all())
    if progress:
        progress(total, 3)
    matured_conversation_ids = {
        conversation.id for conversation in conversations
        if conversation.lead_status in ('interested', 'ready_to_sell') or conversation.meeting_booked
    }

    assigned = session.query(Lead).filter(
        Lead.user_id == current_user.id,
        Lead.assigned_agent_id.isnot(None),
    ).all()
    if progress:
        progress(total, 4)
    in_progress = sum(1 for lead in assigned if _assignment_stage(lead) in IN_PROGRESS_STAGES)
    completed = sum(1 for lead in assigned if _assignment_stage(lead) in COMPLETED_STAGES)
    booked = session.query(Conversation).filter(
        Conversation.user_id == current_user.id,
        Conversation.meeting_booked.is_(True),
    ).count()
    if progress:
        progress(total, 5)

    return {
        'campaigns_started': len(campaigns),
        'campaigns_completed': len(sent_campaign_ids),
        'campaigns_in_draft': sum(1 for campaign in campaigns if campaign.status == 'draft'),
        'campaign_to_matured_rate': round(
            (len(matured_conversation_ids) / len(campaign_leads) * 100) if campaign_leads else 0,
            1,
        ),
        'total_assigned': len(assigned),
        'total_in_progress': in_progress,
        'total_completed': completed,
        'total_booked': booked,
    }


def _load_broker_overview_job(job_id: str, user_id: int) -> None:
    session = SessionLocal()
    try:
        user = session.get(User, user_id)
        if user is None:
            raise LookupError('The account no longer exists.')
        _require_broker(user)
        payload = _broker_overview_payload(
            session,
            user,
            progress=lambda total, loaded: query_progress.update(
                job_id, total=total, loaded=loaded,
            ),
        )
        query_progress.complete(job_id, payload, total=5)
    except Exception as error:  # noqa: BLE001 - the job must report every failure
        query_progress.fail(job_id, error)
    finally:
        session.close()


@router.post('/overview/load', status_code=202)
def start_broker_overview_load(
    background: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Start the five measured query stages used by the broker overview."""
    _require_broker(current_user)
    try:
        job = query_progress.create(current_user.id, 'broker-overview')
    except query_progress.ProgressStoreUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error))
    background.add_task(_load_broker_overview_job, job['job_id'], current_user.id)
    return {'job_id': job['job_id']}


@router.get('/mine')
def list_my_assigned_leads(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Read-only queue containing only leads explicitly assigned to this agent."""
    _require_agent(current_user)
    leads = (
        session.query(Lead)
        .join(Conversation, Conversation.id == Lead.conversation_id)
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(
            Lead.assigned_agent_id == current_user.id,
            Message.direction == 'inbound',
        )
        .distinct()
        .order_by(Lead.last_activity_at.desc(), Lead.id.desc())
        .all()
    )
    broker = (session.query(User).filter(User.id == current_user.assigned_broker_id).first()
              if current_user.assigned_broker_id else None)
    return {
        'leads': [_serialize_assignment(session, lead) for lead in leads],
        'broker': ({'id': broker.id, 'full_name': broker.full_name, 'email': broker.email}
                   if broker else None),
    }


@router.get('/mine/overview')
def my_assignment_overview(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Persisted assignment and follow-up metrics for the Agent Overview."""

    _require_agent(current_user)

    leads = (
        session.query(Lead)
        .filter(
            Lead.assigned_agent_id == current_user.id
        )
        .all()
    )

    counts = {
        stage: sum(
            1
            for lead in leads
            if _assignment_stage(lead) == stage
        )
        for stage in ASSIGNMENT_STAGES
    }

    conversation_ids = [
        lead.conversation_id
        for lead in leads
        if lead.conversation_id
    ]

    conversations = (
        []
        if not conversation_ids
        else session.query(Conversation)
        .filter(
            Conversation.id.in_(
                conversation_ids
            )
        )
        .all()
    )

    messages = (
        []
        if not conversation_ids
        else session.query(Message)
        .filter(
            Message.conversation_id.in_(
                conversation_ids
            )
        )
        .order_by(
            Message.conversation_id,
            Message.created_at,
            Message.id,
        )
        .all()
    )

    latest_by_conversation = {}

    for message in messages:
        latest_by_conversation[
            message.conversation_id
        ] = message

    attention = sum(
        1
        for conversation in conversations
        if latest_by_conversation.get(
            conversation.id
        )
        and latest_by_conversation[
            conversation.id
        ].direction == 'inbound'
    )

    appointments = sum(
        1
        for conversation in conversations
        if conversation.meeting_booked
    )

    completed_seconds = []

    for lead in leads:
        if (
            _assignment_stage(lead)
            not in COMPLETED_STAGES
        ):
            continue

        totals = _stage_seconds(lead)

        completed_seconds.append(
            sum(
                totals[stage]
                for stage in (
                    'new',
                    *IN_PROGRESS_STAGES,
                )
            )
        )

    total = len(leads)
    response_analytics = (
        get_agent_response_analytics(
            session=session,
            agent_id=current_user.id,
        )
    )
    upcoming_events = get_upcoming_calendar_events(
    session=session,
    agent_id=current_user.id,
)

    return {
        'assigned_leads': total,

        'new_assignments':
            counts['new'],

        'in_progress_leads':
            sum(
                counts[stage]
                for stage
                in IN_PROGRESS_STAGES
            ),

        'completed_leads':
            sum(
                counts[stage]
                for stage
                in COMPLETED_STAGES
            ),

        'replies_requiring_attention':
            attention,

        'appointments_booked':
            appointments,

        'completion_rate':
            round(
                (sum(
                        counts[stage]
                        for stage
                        in COMPLETED_STAGES
                    )
                    / total
                    * 100
                )
                if total
                else 0,
                1,
            ),

        'average_handling_seconds':
            (
                round(
                    sum(completed_seconds)
                    / len(completed_seconds)
                )
                if completed_seconds
                else None
            ),
        'upcoming_events': upcoming_events,
        'average_response_seconds':
            response_analytics[
                'average_response_seconds'
            ],
        'reply_rate':
            response_analytics[
                'reply_rate'
            ],
    }

@router.get('/my-broker')
def get_my_broker(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_agent(current_user)
    broker = (session.query(User).filter(
        User.id == current_user.assigned_broker_id,
        User.role == 'broker',
        User.is_active.is_(True),
    ).first() if current_user.assigned_broker_id else None)
    return {'broker': ({'id': broker.id, 'full_name': broker.full_name, 'email': broker.email}
                       if broker else None)}


@router.post('/round-robin')
def round_robin_assignments(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    """Evenly distribute unassigned replied leads across linked active agents."""

    _require_broker(current_user)

    agents = _linked_agents(
        session,
        current_user,
    )

    if not agents:
        raise HTTPException(
            status_code=400,
            detail=(
                'Link at least one active agent '
                'before automatic assignment.'
            ),
        )

    leads = _replied_leads(
        session,
        current_user,
    )

    unassigned = [
        lead
        for lead in leads
        if lead.assigned_agent_id is None
    ]

    workload = {
        agent.id: sum(
            1
            for lead in leads
            if lead.assigned_agent_id == agent.id
        )
        for agent in agents
    }

    now = datetime.utcnow()

    # Keep track of exactly which agent
    # received each lead.
    assigned_pairs: list[tuple[Lead, User]] = []

    for lead in unassigned:

        agent = min(
            agents,
            key=lambda row: (
                workload[row.id],
                row.full_name or row.email,
                row.id,
            ),
        )

        lead.assigned_agent_id = agent.id
        lead.assignment_stage = 'new'
        lead.assignment_stage_changed_at = now
        lead.assignment_stage_seconds = '{}'

        workload[agent.id] += 1

        assigned_pairs.append(
            (lead, agent)
        )

    if unassigned:

        session.commit()

        for lead, agent in assigned_pairs:

            lead_events.log_event(
                session,
                lead.id,
                lead_events.ASSIGNMENT,
                'assigned',
                actor_type='broker',
                actor_id=current_user.id,
                target_id=agent.id,
                reason='round robin',
            )

            try:
                notify_user(
                    session=session,
                    user_id=agent.id,
                    notification_type="LEAD_ASSIGNED",
                    title="New lead assigned",
                    message=(
                        "A new lead has been assigned to you."
                    ),
                    action_url=f"/dashboard?view=leads&lead_id={lead.id}",
                )

                print(
                    "[ROUND ROBIN NOTIFICATION SENT]",
                    f"lead_id={lead.id}",
                    f"agent_id={agent.id}",
                )

            except Exception as error:
                print(
                    "[LEAD ASSIGNMENT NOTIFICATION FAILED]",
                    f"lead_id={lead.id}",
                    f"agent_id={agent.id}",
                    repr(error),
                )

            _email_assignment(agent, current_user, lead)

    return {
        'assigned': len(unassigned),

        'leads': [
            _serialize_assignment(
                session,
                lead,
            )
            for lead in leads
        ],

        'agents': [
            {
                'id': agent.id,
                'full_name': agent.full_name,
                'email': agent.email,
            }
            for agent in agents
        ],
    }

@router.patch('/mine/{lead_id}/stage')
def update_my_lead_stage(
    lead_id: int,
    payload: LeadAssignmentStageRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_agent(current_user)
    lead = session.query(Lead).filter(
        Lead.id == lead_id,
        Lead.assigned_agent_id == current_user.id,
    ).first()
    if not lead:
        raise HTTPException(status_code=404, detail='Assigned lead not found.')

    current_stage = _assignment_stage(lead)
    if payload.stage != current_stage:
        totals = _stage_seconds(lead)
        # _stage_seconds includes the live current interval; persist that snapshot.
        lead.assignment_stage_seconds = json.dumps(totals, sort_keys=True)
        lead.assignment_stage = payload.stage
        lead.assignment_stage_changed_at = datetime.utcnow()
        if payload.stage != 'new' and lead.conversation_id:
            conversation = session.query(Conversation).filter(
                Conversation.id == lead.conversation_id).first()
            if conversation:
                conversation.lead_status = payload.stage
                if payload.stage == 'dnc':
                    conversation.dnc_alert = True
                    conversation.ai_enabled = False
                    conversation.handled_by = 'broker'
        session.commit()
        session.refresh(lead)
        lead_events.log_event(
            session, lead.id, lead_events.STAGE, 'stage_changed',
            actor_type='agent', actor_id=current_user.id,
            from_value=current_stage, to_value=payload.stage,
        )
    return _serialize_assignment(session, lead)


@router.patch('/{lead_id}')
def assign_lead(
    lead_id: int,
    payload: LeadAssignmentRequest,
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    _require_broker(current_user)

    lead = next((row for row in _replied_leads(session, current_user) if row.id == lead_id), None)
    if not lead:
        raise HTTPException(status_code=404, detail='Replied lead not found.')
    assigned_agent = None
    previous_agent_id = lead.assigned_agent_id
    if payload.agent_id is None:
        lead.assigned_agent_id = None
    else:
        agent = next((row for row in _linked_agents(session, current_user)
                      if row.id == payload.agent_id), None)
        if not agent:
            raise HTTPException(status_code=400, detail='Select an active agent assigned to you.')

        assigned_agent = agent
        if lead.assigned_agent_id != agent.id:
            lead.assigned_agent_id = agent.id
            lead.assignment_stage = 'new'
            lead.assignment_stage_changed_at = datetime.utcnow()
            lead.assignment_stage_seconds = '{}'

    session.commit()
    session.refresh(lead)

    if previous_agent_id != lead.assigned_agent_id:
        if lead.assigned_agent_id is None:
            event_type = 'revoked'
        elif previous_agent_id is None:
            event_type = 'assigned'
        else:
            event_type = 'reassigned'
        lead_events.log_event(
            session, lead.id, lead_events.ASSIGNMENT, event_type,
            actor_type='broker', actor_id=current_user.id, target_id=lead.assigned_agent_id,
            from_value=str(previous_agent_id) if previous_agent_id else None,
            to_value=str(lead.assigned_agent_id) if lead.assigned_agent_id else None,
        )

        if (
            lead.assigned_agent_id
            is not None
            and assigned_agent
            is not None
        ):

            try:

                notification_title = (
                    "Lead reassigned to you"
                    if previous_agent_id
                    is not None
                    else "New lead assigned"
                )
                print("hi noti")
                notify_user(
                    session=session,
                    user_id=
                        assigned_agent.id,
                    notification_type=
                        "LEAD_ASSIGNED",
                    title=
                        notification_title,
                    message=(
                        "A lead has been "
                        "assigned to you."
                    ),
                    action_url=
                        f"/dashboard?view=leads&lead_id={lead.id}",
                )

            except Exception as error:

                print(
                    "[LEAD ASSIGNMENT "
                    "NOTIFICATION FAILED]",
                    error,
                )

            _email_assignment(
                assigned_agent,
                current_user,
                lead,
                reassigned=previous_agent_id is not None,
            )

    return _serialize_assignment(session, lead)



def get_upcoming_calendar_events(
    session: Session,
    agent_id: int,
    limit: int = 5,
) -> list[dict]:

    now = datetime.utcnow()

    bookings = (
        session.query(Booking)
        .filter(
            Booking.user_id == agent_id,
            Booking.start_at >= now,
        )
        .order_by(
            Booking.start_at.asc(),
            Booking.id.asc(),
        )
        .limit(limit)
        .all()
    )

    return [
        {
            "id": booking.id,
            "name": booking.name,
            "title": booking.title,
            "start_at": booking.start_at.replace(tzinfo=timezone.utc) if booking.start_at.tzinfo is None else booking.start_at,
            "end_at": (booking.end_at.replace(tzinfo=timezone.utc) if booking.end_at.tzinfo is None else booking.end_at) if booking.end_at else None,
        }
        for booking in bookings
    ]
