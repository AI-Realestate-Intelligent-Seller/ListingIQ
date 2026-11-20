"""Build, schedule, launch, and cancel A/B/n experiments."""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..leads import campaign as campaign_service
from ..leads import template as message_template
from ..models import Campaign, Experiment, Message, User
from ..tenancy import brokerage_user_ids

TEST_DIMENSIONS = {'tone', 'length', 'reason', 'open_question', 'other'}
WINNER_METRICS = {'reply_rate', 'positive_response_rate'}
ALLOCATION_MODES = {'balanced', 'random'}
MESSAGE_COST = 0.0195


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _outreach_conversation_counts(session: Session, experiment_ids: list[int]) -> dict[int, int]:
    """Distinct owner threads across every variant in each experiment."""
    if not experiment_ids:
        return {}
    rows = (session.query(
                Campaign.experiment_id,
                func.count(func.distinct(Message.conversation_id)),
            )
            .join(Message, Message.campaign_id == Campaign.id)
            .filter(
                Campaign.experiment_id.in_(experiment_ids),
                Message.event_type == 'outreach.initial',
            )
            .group_by(Campaign.experiment_id)
            .all())
    return {experiment_id: count for experiment_id, count in rows}


def serialize(experiment: Experiment, session: Session,
              outreach_conversations: int | None = None) -> dict:
    try:
        variants = json.loads(experiment.variant_config or '[]')
    except ValueError:
        variants = []
    try:
        campaign_ids = json.loads(experiment.campaign_ids or '[]')
    except ValueError:
        campaign_ids = []
    campaign_ids = campaign_ids if isinstance(campaign_ids, list) else []
    if outreach_conversations is None:
        outreach_conversations = _outreach_conversation_counts(
            session, [experiment.id],
        ).get(experiment.id, 0)
    return {
        'id': experiment.id,
        'name': experiment.name,
        'goal_notes': experiment.goal_notes or '',
        'test_dimension': experiment.test_dimension,
        'test_dimension_other': experiment.test_dimension_other or '',
        'winner_metric': experiment.winner_metric,
        'allocation_mode': experiment.allocation_mode,
        'timezone': experiment.timezone,
        'scheduled_at': experiment.scheduled_at,
        'status': experiment.status,
        'variants': variants if isinstance(variants, list) else [],
        'campaign_ids': campaign_ids,
        'total_leads': experiment.total_leads,
        'outreach_conversations': outreach_conversations,
        'expected_max_cost': experiment.expected_max_cost,
        'error': experiment.error or '',
        'launched_at': experiment.launched_at,
        'cancelled_at': experiment.cancelled_at,
        'created_at': experiment.created_at,
    }


def owned(session: Session, user: User, experiment_id: int) -> Experiment:
    row = (session.query(Experiment)
           .filter(Experiment.id == experiment_id,
                   Experiment.user_id.in_(brokerage_user_ids(session, user)))
           .first())
    if row is None:
        raise LookupError('Experiment not found.')
    return row


def list_experiments(session: Session, user: User) -> list[dict]:
    rows = (session.query(Experiment)
            .filter(Experiment.user_id.in_(brokerage_user_ids(session, user)))
            .order_by(Experiment.created_at.desc(), Experiment.id.desc()).all())
    conversation_counts = _outreach_conversation_counts(
        session, [row.id for row in rows],
    )
    return [serialize(row, session, conversation_counts.get(row.id, 0)) for row in rows]


def create(session: Session, user: User, payload) -> Experiment:
    dimensions = [value.strip() for value in payload.test_dimension.split(',') if value.strip()]
    if not dimensions or len(dimensions) != len(set(dimensions)) or any(
        value not in TEST_DIMENSIONS for value in dimensions
    ):
        raise ValueError('Choose tone, length, reason, open question, or other.')
    if 'other' in dimensions and not (payload.test_dimension_other or '').strip():
        raise ValueError('Describe what this experiment is testing.')
    if payload.winner_metric not in WINNER_METRICS:
        raise ValueError('Choose reply rate or positive response rate as the winner metric.')
    if payload.allocation_mode not in ALLOCATION_MODES:
        raise ValueError('Allocation must be balanced or random.')
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError:
        raise ValueError('Choose a valid IANA time zone.')
    seen: set[int] = set()
    labels: set[str] = set()
    for variant in payload.variants:
        label = variant.label.strip().lower()
        if label in labels:
            raise ValueError('Every experiment group needs a unique label.')
        labels.add(label)
        message_template.validate(variant.message_template)
        if variant.followup_enabled:
            message_template.validate(variant.followup_template or '')
            if variant.followup_delay_hours is None:
                raise ValueError(f'Choose a follow-up delay for {variant.label}.')
        overlap = seen.intersection(variant.lead_ids)
        if overlap or len(set(variant.lead_ids)) != len(variant.lead_ids):
            raise ValueError('A lead cannot appear in more than one experiment group.')
        seen.update(variant.lead_ids)

    if not payload.launch_now and payload.scheduled_at is None:
        raise ValueError('Choose when the experiment should launch or send it now.')
    scheduled_at = (
        datetime.utcnow() if payload.launch_now else _naive_utc(payload.scheduled_at)
    )
    experiment = Experiment(
        user_id=user.id,
        name=payload.name.strip(),
        goal_notes=(payload.goal_notes or '').strip(),
        test_dimension=','.join(dimensions),
        test_dimension_other=(payload.test_dimension_other or '').strip(),
        winner_metric=payload.winner_metric,
        allocation_mode=payload.allocation_mode,
        timezone=payload.timezone,
        scheduled_at=scheduled_at,
        status='scheduled',
        created_at=datetime.utcnow(),
    )
    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    campaigns: list[Campaign] = []
    config: list[dict] = []
    try:
        for index, variant in enumerate(payload.variants):
            campaign, not_added = campaign_service.create_draft(
                session, user, variant.lead_ids,
                name=f'{experiment.name} · {variant.label}',
                template=variant.message_template,
                group_mode=True,
            )
            campaigns.append(campaign)
            if not_added:
                raise ValueError(
                    f'{len(not_added)} leads in {variant.label} became unavailable. '
                    'Return to the audience step and create the groups again.'
                )
            campaign.experiment_id = experiment.id
            campaign.experiment_variant = variant.label
            campaign.followup_enabled = variant.followup_enabled
            campaign.followup_template = (
                variant.followup_template.strip() if variant.followup_enabled else None
            )
            campaign.followup_delay_hours = (
                variant.followup_delay_hours if variant.followup_enabled else None
            )
            session.add(campaign)
            session.commit()
            config.append({
                'label': variant.label,
                'message_template': variant.message_template,
                'campaign_id': campaign.id,
                'lead_count': len(variant.lead_ids),
                'followup_enabled': variant.followup_enabled,
                'followup_template': variant.followup_template or '',
                'followup_delay_hours': variant.followup_delay_hours,
                'color_index': index,
            })
    except Exception:
        for campaign in campaigns:
            if campaign.status == 'draft':
                campaign_service.release_draft_members(session, user, campaign)
                session.delete(campaign)
        session.delete(experiment)
        session.commit()
        raise

    followup_messages = sum(
        item['lead_count'] for item in config if item['followup_enabled']
    )
    experiment.variant_config = json.dumps(config)
    experiment.campaign_ids = json.dumps([campaign.id for campaign in campaigns])
    experiment.total_leads = len(seen)
    experiment.expected_max_cost = round((len(seen) + followup_messages) * MESSAGE_COST, 4)
    session.add(experiment)
    session.commit()
    session.refresh(experiment)
    return experiment


def cancel(session: Session, user: User, experiment: Experiment) -> Experiment:
    if experiment.status != 'scheduled':
        raise ValueError('Only a scheduled experiment can be cancelled.')
    campaigns = session.query(Campaign).filter(Campaign.experiment_id == experiment.id).all()
    for campaign in campaigns:
        if campaign.status != 'draft':
            raise ValueError('This experiment has already started and can no longer be cancelled.')
        campaign_service.release_draft_members(session, user, campaign)
        session.delete(campaign)
    experiment.status = 'cancelled'
    experiment.cancelled_at = datetime.utcnow()
    session.add(experiment)
    session.commit()
    session.refresh(experiment)
    return experiment
