"""Build, schedule, launch, and cancel A/B/n experiments."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..leads import campaign as campaign_service
from ..leads import template as message_template
from ..models import (
    Campaign, Conversation, Experiment, Lead, LeadEvent, Message,
    OutboundSmsJob, User,
)
from ..tenancy import brokerage_user_ids

TEST_DIMENSIONS = {'tone', 'length', 'reason', 'open_question', 'other'}
WINNER_METRICS = {'reply_rate', 'positive_response_rate'}
ALLOCATION_MODES = {'balanced', 'random'}
MESSAGE_COST = 0.0195
DEFAULT_FOLLOWUP_DELAY_HOURS = 48.0


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


def _lookup_reason(lead: Lead) -> str | None:
    """Explain a persisted live-number lookup that prevented submission."""
    try:
        details = json.loads(lead.details or '{}')
    except (TypeError, ValueError):
        details = {}
    current_phone = campaign_service.normalize_phone(lead.phone or '')
    candidates = details.get('_phone_numbers') or []
    for candidate in candidates if isinstance(candidates, list) else []:
        if not isinstance(candidate, dict):
            continue
        candidate_phone = campaign_service.normalize_phone(
            str(candidate.get('phone') or candidate.get('number') or ''),
        )
        if current_phone and candidate_phone != current_phone:
            continue
        lookup = candidate.get('telnyx_lookup')
        if not isinstance(lookup, dict):
            continue
        status = str(lookup.get('status') or candidate.get('lookup_status') or '')
        line_type = str(lookup.get('line_type') or candidate.get('type') or '').strip()
        if status == 'unknown':
            return 'Phone type could not be verified as mobile by Telnyx. No message was sent.'
        if status == 'error':
            return 'The phone lookup failed, so no message was sent.'
        if status == 'verified' and line_type and line_type != 'mobile':
            label = line_type.replace('_', ' ').replace('-', ' ')
            return f'Number is {label}; campaigns send only to mobile numbers.'
    return None


def _outreach_detail(session: Session, experiment: Experiment,
                     campaigns: dict[int, Campaign]) -> tuple[int, list[dict]]:
    """Selected rows that reached an owner, plus terminal rows that did not."""
    campaign_ids = set(campaigns)
    if not campaign_ids:
        return 0, []
    campaign_meta = []
    for campaign_id in campaign_ids:
        campaign_meta.extend((
            LeadEvent.meta.like(f'%"campaign_id": {campaign_id}%'),
            LeadEvent.meta.like(f'%"campaign_id":{campaign_id}%'),
        ))
    events = (session.query(LeadEvent)
              .filter(
                  LeadEvent.event_type.in_(('locked_to_campaign', 'outreach_sent')),
                  or_(*campaign_meta),
              )
              .order_by(LeadEvent.id)
              .all())
    selected: dict[int, int] = {}
    message_by_lead: dict[int, int] = {}
    for event in events:
        try:
            meta = json.loads(event.meta or '{}')
        except (TypeError, ValueError):
            continue
        campaign_id = meta.get('campaign_id')
        if campaign_id not in campaign_ids:
            continue
        if event.event_type == 'locked_to_campaign':
            selected[event.lead_id] = campaign_id
        elif event.event_type == 'outreach_sent' and meta.get('message_id'):
            message_by_lead[event.lead_id] = int(meta['message_id'])

    message_ids = set(message_by_lead.values())
    messages = ({row.id: row for row in session.query(Message).filter(
        Message.id.in_(message_ids),
    ).all()} if message_ids else {})
    contacted = {
        lead_id for lead_id, message_id in message_by_lead.items()
        if message_id in messages
        and messages[message_id].status not in campaign_service.NON_DELIVERED_STATUSES
    }
    if not selected:
        # Legacy experiments predate lead-history events. Preserve the existing
        # property-based outreach total even though per-lead reasons are absent.
        owner = session.get(User, experiment.user_id)
        if owner is None:
            return 0, []
        _members, delivered, _replied, _positive = campaign_service._counts_by_campaign(
            session, owner, campaign_ids,
        )
        return sum(delivered.values()), []

    missing_ids = set(selected) - contacted
    if not missing_ids:
        return len(contacted), []
    leads = {row.id: row for row in session.query(Lead).filter(
        Lead.id.in_(missing_ids),
    ).all()}
    jobs = (session.query(OutboundSmsJob)
            .filter(
                OutboundSmsJob.campaign_id.in_(campaign_ids),
                OutboundSmsJob.lead_id.in_(missing_ids),
            ).order_by(OutboundSmsJob.id.desc()).all())
    job_reason = {
        job.lead_id: job.last_error for job in jobs
        if job.last_error and job.status in {'skipped', 'failed', 'uncertain'}
    }
    not_contacted = []
    for lead_id in sorted(missing_ids):
        campaign_id = selected[lead_id]
        campaign = campaigns[campaign_id]
        job = next((row for row in jobs if row.lead_id == lead_id), None)
        # Queued work is not a failure. Report it only after a terminal job or
        # after the variant has completed all of its outbound work.
        if campaign.status != 'sent' and not (
            job and job.status in {'skipped', 'failed', 'uncertain'}
        ):
            continue
        lead = leads.get(lead_id)
        message = messages.get(message_by_lead.get(lead_id, -1))
        reason = job_reason.get(lead_id)
        if not reason and message is not None:
            reason = message.failure_reason
        if not reason and lead is not None:
            reason = _lookup_reason(lead)
        if not reason and lead is not None:
            reason = campaign_service.selection_reason(lead)
        not_contacted.append({
            'lead_id': lead_id,
            'owner_name': lead.owner_name if lead else None,
            'property_address': lead.property_address if lead else None,
            'variant': campaign.experiment_variant or campaign.name,
            'reason': reason or 'No opening message was sent.',
        })
    return len(contacted), not_contacted


def serialize(experiment: Experiment, session: Session,
              outreach_conversations: int | None = None,
              include_outreach_detail: bool = False) -> dict:
    try:
        variants = json.loads(experiment.variant_config or '[]')
    except ValueError:
        variants = []
    try:
        campaign_ids = json.loads(experiment.campaign_ids or '[]')
    except ValueError:
        campaign_ids = []
    campaign_ids = campaign_ids if isinstance(campaign_ids, list) else []
    campaigns = ({row.id: row for row in session.query(Campaign).filter(
        Campaign.id.in_(campaign_ids),
    ).all()} if campaign_ids else {})
    followup_started = ({row[0] for row in session.query(Conversation.campaign_id)
                         .join(Message, Message.conversation_id == Conversation.id)
                         .filter(
                             Conversation.campaign_id.in_(campaign_ids),
                             Message.event_type.like('ai.followup.%'),
                         ).distinct().all()} if campaign_ids else set())
    next_followups = (dict(session.query(
        Conversation.campaign_id,
        func.min(Conversation.next_followup_at),
    ).filter(
        Conversation.campaign_id.in_(campaign_ids),
        Conversation.next_followup_at.isnot(None),
    ).group_by(Conversation.campaign_id).all()) if campaign_ids else {})
    enriched_variants = []
    for stored in variants if isinstance(variants, list) else []:
        item = dict(stored)
        campaign_id = item.get('campaign_id')
        campaign = campaigns.get(campaign_id)
        if campaign is not None:
            item['followup_enabled'] = bool(campaign.followup_enabled)
            item['followup_template'] = campaign.followup_template
            item['followup_delay_hours'] = (
                campaign.followup_delay_hours or DEFAULT_FOLLOWUP_DELAY_HOURS
            )
        item['followup_editable'] = bool(
            experiment.status == 'running' and campaign_id not in followup_started
        )
        item['followup_scheduled_at'] = next_followups.get(campaign_id)
        enriched_variants.append(item)
    if outreach_conversations is None:
        outreach_conversations = _outreach_conversation_counts(
            session, [experiment.id],
        ).get(experiment.id, 0)
    # Outreach is finished once the outbound worker has marked every variant
    # 'sent', which it does only after each queued job reaches a terminal
    # state. A variant extended with extra recipients returns to 'sending',
    # so this reports the batch in flight, not the experiment's whole history.
    launched_campaigns = [campaigns[row] for row in campaign_ids if row in campaigns]
    outreach_complete = bool(
        experiment.status == 'running'
        and campaign_ids
        and len(launched_campaigns) == len(campaign_ids)
        and all(row.status == 'sent' for row in launched_campaigns)
    )
    outreached_leads, not_contacted = (
        _outreach_detail(session, experiment, campaigns)
        if include_outreach_detail else (0, [])
    )
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
        'variants': enriched_variants,
        'campaign_ids': campaign_ids,
        'total_leads': experiment.total_leads,
        'outreach_conversations': outreach_conversations,
        'outreached_leads': outreached_leads,
        'not_contacted': not_contacted,
        'outreach_complete': outreach_complete,
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
        # Variant campaigns commit while they are being assembled. Keep the
        # experiment out of the scheduler's runnable state until every variant
        # is linked and the final configuration has been saved. This matters
        # most for Send Now, whose scheduled_at is already due while a large
        # audience can still take many seconds to attach.
        status='building',
        created_at=datetime.utcnow(),
    )
    session.add(experiment)
    session.commit()
    session.refresh(experiment)

    campaigns: list[Campaign] = []
    config: list[dict] = []
    try:
        for index, variant in enumerate(payload.variants):
            try:
                campaign, not_added = campaign_service.create_draft(
                    session, user, variant.lead_ids,
                    name=f'{experiment.name} · {variant.label}',
                    template=variant.message_template,
                    group_mode=True,
                )
            except ValueError as error:
                raise ValueError(
                    f'{variant.label} has no eligible leads remaining. '
                    'Return to the audience step and recreate the groups.'
                ) from error
            campaigns.append(campaign)
            accepted_count = campaign_service.member_count(
                session, user, campaign,
            )
            if accepted_count < 1:
                raise ValueError(
                    f'{variant.label} has no eligible leads remaining. '
                    'Return to the audience step and recreate the groups.'
                )
            campaign.experiment_id = experiment.id
            campaign.experiment_variant = variant.label
            campaign.followup_enabled = variant.followup_enabled
            campaign.followup_template = (
                variant.followup_template.strip() if variant.followup_enabled else None
            )
            followup_delay = (
                variant.followup_delay_hours or DEFAULT_FOLLOWUP_DELAY_HOURS
            )
            campaign.followup_delay_hours = (
                followup_delay if variant.followup_enabled else None
            )
            session.add(campaign)
            session.commit()
            config.append({
                'label': variant.label,
                'message_template': variant.message_template,
                'campaign_id': campaign.id,
                'lead_count': accepted_count,
                'requested_lead_count': len(set(variant.lead_ids)),
                'skipped_lead_count': len(not_added),
                'followup_enabled': variant.followup_enabled,
                'followup_template': variant.followup_template or '',
                'followup_delay_hours': followup_delay,
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
    accepted_leads = sum(item['lead_count'] for item in config)
    experiment.variant_config = json.dumps(config)
    experiment.campaign_ids = json.dumps([campaign.id for campaign in campaigns])
    experiment.total_leads = accepted_leads
    experiment.expected_max_cost = round(
        (accepted_leads + followup_messages) * MESSAGE_COST, 4,
    )
    experiment.status = 'scheduled'
    session.add(experiment)
    session.commit()
    session.refresh(experiment)
    return experiment


def update_followup(session: Session, user: User, experiment: Experiment,
                    campaign_id: int, payload) -> Experiment:
    """Edit one launched variant until its first follow-up is sent."""
    if experiment.status != 'running':
        raise ValueError('Follow-ups can be edited after the experiment launches.')
    campaign = (session.query(Campaign)
                .filter(
                    Campaign.id == campaign_id,
                    Campaign.experiment_id == experiment.id,
                    Campaign.user_id.in_(brokerage_user_ids(session, user)),
                ).with_for_update().first())
    if campaign is None:
        raise LookupError('Experiment variant not found.')

    conversations = (session.query(Conversation)
                     .filter(Conversation.campaign_id == campaign.id)
                     .with_for_update().all())
    conversation_ids = [row.id for row in conversations]
    if conversation_ids and session.query(Message.id).filter(
        Message.conversation_id.in_(conversation_ids),
        Message.event_type.like('ai.followup.%'),
    ).first():
        raise ValueError('This variant has already started its follow-up and is locked.')

    inbound_ids = ({row[0] for row in session.query(Message.conversation_id).filter(
        Message.conversation_id.in_(conversation_ids),
        Message.direction == 'inbound',
    ).distinct().all()} if conversation_ids else set())
    if campaign.followup_enabled and any(
        row.id not in inbound_ids
        and row.followup_started_at is not None
        and row.next_followup_at is None
        and not row.followup_attempt_count
        for row in conversations
    ):
        raise ValueError('This variant is starting its follow-up now and is locked.')

    template = (payload.followup_template or '').strip()
    delay = float(payload.followup_delay_hours or DEFAULT_FOLLOWUP_DELAY_HOURS)
    if payload.followup_enabled:
        message_template.validate(template)
    elif not template:
        template = campaign.followup_template or ''

    initial_rows = (session.query(Message.conversation_id, func.min(Message.created_at))
                    .filter(
                        Message.conversation_id.in_(conversation_ids),
                        Message.campaign_id == campaign.id,
                        Message.event_type == 'outreach.initial',
                    ).group_by(Message.conversation_id).all()
                    if conversation_ids else [])
    initial_at = dict(initial_rows)
    for conversation in conversations:
        if conversation.id in inbound_ids:
            conversation.next_followup_at = None
        elif payload.followup_enabled:
            cadence_start = (
                initial_at.get(conversation.id)
                or conversation.followup_started_at
                or campaign.sent_at
                or datetime.utcnow()
            )
            conversation.followup_started_at = cadence_start
            conversation.next_followup_at = cadence_start + timedelta(hours=delay)
        else:
            conversation.next_followup_at = None
        session.add(conversation)

    campaign.followup_enabled = payload.followup_enabled
    campaign.followup_template = template or None
    campaign.followup_delay_hours = delay
    session.add(campaign)

    try:
        config = json.loads(experiment.variant_config or '[]')
    except ValueError:
        config = []
    for item in config if isinstance(config, list) else []:
        if item.get('campaign_id') == campaign.id:
            item['followup_enabled'] = payload.followup_enabled
            item['followup_template'] = template
            item['followup_delay_hours'] = delay
    experiment.variant_config = json.dumps(config)
    followup_messages = sum(
        int(item.get('lead_count') or 0)
        for item in config
        if item.get('followup_enabled')
    )
    experiment.expected_max_cost = round(
        (experiment.total_leads + followup_messages) * MESSAGE_COST, 4,
    )
    session.add(experiment)
    session.commit()
    session.refresh(experiment)
    return experiment


def add_random_leads(session: Session, user: User, experiment: Experiment,
                     campaign_id: int, count: int) -> Experiment:
    """Add random Lead Pool recipients to one running experiment variant."""
    if experiment.status != 'running':
        raise ValueError(
            'Random leads can be added only after the experiment launches.'
        )
    campaign = (session.query(Campaign)
                .filter(
                    Campaign.id == campaign_id,
                    Campaign.experiment_id == experiment.id,
                    Campaign.user_id.in_(brokerage_user_ids(session, user)),
                ).first())
    if campaign is None:
        raise LookupError('Experiment variant not found.')
    result = campaign_service.add_random_recipients(
        session, user, campaign, count,
    )
    added = int(result['added'])

    try:
        config = json.loads(experiment.variant_config or '[]')
    except ValueError:
        config = []
    for item in config if isinstance(config, list) else []:
        if item.get('campaign_id') == campaign.id:
            item['lead_count'] = int(item.get('lead_count') or 0) + added
            item['requested_lead_count'] = int(
                item.get('requested_lead_count') or item['lead_count'] - added
            ) + count
            item['skipped_lead_count'] = int(item.get('skipped_lead_count') or 0) + (
                count - added
            )
            break
    experiment.variant_config = json.dumps(config)
    experiment.total_leads = int(experiment.total_leads or 0) + added
    followup_messages = sum(
        int(item.get('lead_count') or 0)
        for item in config
        if item.get('followup_enabled')
    )
    experiment.expected_max_cost = round(
        (experiment.total_leads + followup_messages) * MESSAGE_COST, 4,
    )
    session.add(experiment)
    session.commit()
    session.refresh(experiment)
    return experiment


def rename(session: Session, user: User, experiment: Experiment, name: str) -> Experiment:
    """Rename the experiment and keep its variant campaign labels aligned."""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError('Give this experiment a name.')
    campaigns = (session.query(Campaign)
                 .filter(
                     Campaign.experiment_id == experiment.id,
                     Campaign.user_id.in_(brokerage_user_ids(session, user)),
                 ).all())
    experiment.name = cleaned
    for campaign in campaigns:
        label = campaign.experiment_variant or f'Variant {campaign.id}'
        campaign.name = f'{cleaned} · {label}'
        session.add(campaign)
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
