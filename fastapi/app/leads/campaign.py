"""Campaigns — turning selected leads into user-handled conversations.

A campaign is a named batch with one message template. The broker selects leads
in the pool, which creates a *draft* holding them, names it and writes the
opening message in the Campaigns tab, then sends. Sending renders the template
once per lead, and opens a conversation for the user to handle. Bobbie can be
explicitly handed the thread later from Follow-ups.

Anything that cannot be sent is reported back per lead rather than failing the
whole batch — a broker selecting forty rows should not lose thirty-nine of them
to one bad phone number.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..core.config import settings
from ..models import Campaign, Conversation, Lead, LeadEvent, Message, OutboundSmsJob, User
from ..sms import number_lookup
from ..sms import service as sms_service
from ..sms.outreach import build_single_lead_context
from ..sms.followup_scheduler import schedule_initial_followup
from ..tenancy import brokerage_user_ids
from . import address as address_key
from . import events as lead_events
from . import reason_suggest
from . import template as message_template
from .catalog import SIGNALS, signal_label
from .importer import normalize_phone
from .service import (conversation_property_details, derive_stage, outreach_reason,
                      predefined_reason, serialize, split_signals)

PROVIDER_LEAD_SOURCE = 'provider_distribution'
PROVIDER_CONTACT_SOURCE = 'provider_distribution_contact'
CAMPAIGN_BATCH_SIZE = 250
MAIN_SIGNAL_BASES = {'single_signal', 'source_verified', 'broker_selected'}
NO_SIGNAL_REASON_KEY = '__no_signal__'
_REASON_SIGNAL_UNSET = object()


def _batches(values: list, size: int = CAMPAIGN_BATCH_SIZE):
    """Yield bounded slices while retaining the caller's stable order."""
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _group_state(campaign: Campaign) -> dict | None:
    """Parsed group-mode state; None is the legacy campaign marker."""
    if campaign.reason_groups is None:
        return None
    try:
        value = json.loads(campaign.reason_groups)
    except (TypeError, ValueError):
        value = {}
    groups = value.get('groups') if isinstance(value, dict) else None
    snapshot = value.get('reason_signal_snapshot') if isinstance(value, dict) else None
    return {
        'version': 1,
        'groups': groups if isinstance(groups, dict) else {},
        'reason_signal_snapshot': snapshot if isinstance(snapshot, dict) else None,
    }


def _property_key(lead: Lead) -> str:
    """Stable property identity shared by a provider master and phone clones."""
    return _property_key_values(lead.id, lead.property_address)


def _property_key_values(lead_id: int, property_address: str | None) -> str:
    return address_key.canonical(property_address) or f'__lead_{lead_id}'


def _valid_signals(raw_signals: str | None) -> set[str]:
    return {
        signal for signal in str(raw_signals or '').split(',') if signal in SIGNALS
    }


def _reason_is_required(campaign: Campaign) -> bool:
    return 'reason' in {
        token.lower() for token in message_template.TOKEN_PATTERN.findall(
            campaign.message_template or '')
    }


def _campaign_reason_context(session: Session, user: User,
                             campaign: Campaign,
                             state: dict | None | object = _REASON_SIGNAL_UNSET,
                             reason_required: bool | None = None) -> dict:
    """Resolve one frequency-based Reason Signal per distinct property."""
    if state is _REASON_SIGNAL_UNSET:
        state = _group_state(campaign)
    if reason_required is None:
        reason_required = _reason_is_required(campaign)
    rows = (_members_query(session, user, campaign)
            .with_entities(
                Lead.id, Lead.property_address, Lead.signals,
                Lead.main_signal, Lead.main_signal_basis, Lead.outreach_reason,
            ).all())
    properties: dict[str, list] = {}
    for row in rows:
        key = _property_key_values(row.id, row.property_address)
        properties.setdefault(key, []).append(row)

    property_signals: dict[str, set[str]] = {}
    frequencies: dict[str, int] = {}
    for key, family in properties.items():
        valid = {
            signal for row in family for signal in _valid_signals(row.signals)
        }
        property_signals[key] = valid
        for signal in valid:
            frequencies[signal] = frequencies.get(signal, 0) + 1

    snapshot = state.get('reason_signal_snapshot') if isinstance(state, dict) else None
    finalized = bool(snapshot and snapshot.get('finalized'))
    stored = snapshot.get('properties') if finalized else None
    stored = stored if isinstance(stored, dict) else {}
    assignments: dict[str, str | None] = {}
    for key, family in properties.items():
        valid = property_signals[key]
        if len(valid) == 0:
            assignments[key] = NO_SIGNAL_REASON_KEY
            continue
        if len(valid) == 1:
            assignments[key] = next(iter(valid))
            continue
        if finalized:
            saved = stored.get(key)
            assignments[key] = saved if saved in valid else None
            continue
        highest = max(frequencies[signal] for signal in valid)
        tied = {signal for signal in valid if frequencies[signal] == highest}
        if len(tied) == 1:
            assignments[key] = next(iter(tied))
            continue
        verified = {
            row.main_signal for row in family
            if row.main_signal_basis == 'source_verified' and row.main_signal in tied
        }
        assignments[key] = next(iter(verified)) if len(verified) == 1 else None
    return {
        'leads': rows,
        'properties': properties,
        'property_signals': property_signals,
        'frequencies': frequencies,
        'assignments': assignments,
        'finalized': finalized,
        'state': state,
        'reason_required': reason_required,
    }


def _finalize_reason_signal_snapshot(session: Session, user: User,
                                     campaign: Campaign) -> dict:
    """Persist only frequency-dependent multi-signal property assignments."""
    context = _campaign_reason_context(session, user, campaign)
    if context['finalized'] or context['state'] is None:
        return context
    compact = {
        key: context['assignments'][key]
        for key, signals in context['property_signals'].items()
        if len(signals) > 1
    }
    state = context['state'] or {'version': 1, 'groups': {}}
    state['reason_signal_snapshot'] = {
        'finalized': True,
        'properties': compact,
    }
    campaign.reason_groups = json.dumps(state, separators=(',', ':'), sort_keys=True)
    session.flush()
    context['finalized'] = True
    context['state'] = state
    return context


def _snapshot_reason_signal(campaign: Campaign, lead: Lead,
                            state: dict | None = None) -> str | None:
    """Read a finalized assignment without re-running campaign frequencies."""
    signals = _valid_signals(lead.signals)
    if not signals:
        return NO_SIGNAL_REASON_KEY
    if len(signals) == 1:
        return next(iter(signals))
    state = state if state is not None else (_group_state(campaign) or {})
    snapshot = state.get('reason_signal_snapshot') or {}
    properties = snapshot.get('properties') if snapshot.get('finalized') else {}
    saved = properties.get(_property_key(lead)) if isinstance(properties, dict) else None
    return saved if saved in signals else None


def resolve_reason(campaign: Campaign, lead: Lead,
                   reason_signal=_REASON_SIGNAL_UNSET,
                   *, state: dict | None | object = _REASON_SIGNAL_UNSET,
                   reason_required: bool | None = None) -> tuple[str, str, bool]:
    """One authoritative reason for preview, queueing and conversation context."""
    if state is _REASON_SIGNAL_UNSET:
        state = _group_state(campaign)
    if state is None:
        return outreach_reason(lead, campaign.outreach_reason or ''), 'legacy', False

    if reason_required is None:
        reason_required = _reason_is_required(campaign)
    if not reason_required:
        return '', 'not_required', False

    signals = _valid_signals(lead.signals)
    if reason_signal is _REASON_SIGNAL_UNSET:
        snapshot = state.get('reason_signal_snapshot') or {}
        if snapshot.get('finalized'):
            reason_signal = _snapshot_reason_signal(campaign, lead, state)
        else:
            # Backward-compatible direct-call fallback. Campaign preview and Send
            # always pass the campaign-scoped frequency result explicitly.
            reason_signal = lead.main_signal if lead.main_signal in signals else (
                next(iter(signals)) if len(signals) == 1 else None)

    if reason_signal == NO_SIGNAL_REASON_KEY:
        if (lead.outreach_reason or '').strip():
            return lead.outreach_reason.strip().rstrip('.'), 'vendor', False
        group = state['groups'].get(NO_SIGNAL_REASON_KEY)
        if isinstance(group, dict) and group.get('confirmed'):
            reason = str(group.get('reason') or '').strip()
            if reason:
                return reason, 'confirmed_group', False
        return '', 'needs_review', True

    group = state['groups'].get(reason_signal) if reason_signal else None
    if isinstance(group, dict) and group.get('confirmed'):
        reason = str(group.get('reason') or '').strip()
        if reason:
            return reason, 'confirmed_group', False
    if (lead.outreach_reason or '').strip():
        return lead.outreach_reason.strip().rstrip('.'), 'vendor', False
    catalog_reason = predefined_reason(reason_signal)
    if catalog_reason:
        return catalog_reason, 'catalog', False
    return '', 'needs_review', True


def _update_reason_group(campaign: Campaign, update: dict) -> None:
    if campaign.status != 'draft':
        raise ValueError('This campaign has already been sent; its reasons cannot change.')
    signal = str(update.get('signal') or '').strip()
    if signal not in SIGNALS and signal != NO_SIGNAL_REASON_KEY:
        raise ValueError('Choose a valid signal for this reason group.')
    reason = str(update.get('reason') or '').strip()[:300]
    confirmed = bool(update.get('confirmed'))
    if confirmed and not reason:
        raise ValueError('Write a reason before confirming this group.')
    if confirmed and signal == NO_SIGNAL_REASON_KEY:
        # These properties have no verified signal, so their shared reason may
        # not state a listing status, distress, value or any other fact.
        problem = reason_suggest.neutral_reason_problem(reason)
        if problem:
            raise ValueError(
                'No Signal properties have no verified facts, so their reason must '
                'stay neutral: say you are reaching out and ask about selling '
                f'(found {problem.replace("_", " ")}).')
    state = _group_state(campaign) or {'version': 1, 'groups': {}}
    state['groups'][signal] = {'reason': reason, 'confirmed': confirmed}
    campaign.reason_groups = json.dumps(state, separators=(',', ':'), sort_keys=True)


# Said the same way everywhere a property turns out to be spoken for, and
# deliberately naming nobody: the caller learns the property is taken, not who
# took it.
CLAIMED_ELSEWHERE = 'Another brokerage is already contacting this property.'


# Already texted this property ourselves. Distinct from the message below,
# because the broker can see their own thread and act on it.
CLAIMED_HERE = 'This property already has a conversation in the SMS tab.'


class Claims:
    """Which property/contact pairs are already spoken for, and by whom.

    `ours` are properties this brokerage has already texted about — visible to
    the broker in the SMS tab. `theirs` are properties another brokerage got to
    first, reported without naming anyone.
    """

    def __init__(self, ours: set[tuple[str, str]], theirs: set[tuple[str, str]]):
        self.ours = ours
        self.theirs = theirs

    def reason_for(self, lead: Lead) -> str | None:
        property_key = address_key.canonical(lead.property_address)
        phone_key = normalize_phone(lead.phone or '')
        if not property_key or not phone_key:
            return None
        key = (property_key, phone_key)
        if key in self.theirs:
            return CLAIMED_ELSEWHERE
        if key in self.ours:
            return CLAIMED_HERE
        return None


def _recipient_key(lead: Lead) -> tuple[str, str] | None:
    property_key = address_key.canonical(lead.property_address)
    phone_key = normalize_phone(lead.phone or '')
    return (property_key, phone_key) if property_key and phone_key else None


NO_CLAIMS = Claims(set(), set())


def _blocking_reason(lead: Lead, claims: Claims = NO_CLAIMS) -> str | None:
    if lead.dnc:
        return 'On the do-not-contact list.'
    if not lead.phone:
        return 'No usable phone number.'
    if not lead.property_address:
        return 'No property address for Bobbie to reference.'
    if lead.conversation_id is not None:
        return 'Already texted in a campaign.'
    return claims.reason_for(lead)


def claims_on(session: Session, scope: list[int], leads: list[Lead]) -> Claims:
    """Which property/contact pairs were already campaigned, by us or others.

    A property can legitimately have multiple owners or contacts. Therefore an
    address already contacted at one phone number does not block a different
    phone number at that address. Only the same normalized property-and-phone
    pair is claimed. The first brokerage to actually send owns that pair — an
    unsent draft reserves nothing, and a thread started by hand from the SMS
    tab is not a campaign claim either.

    The claim is read from the outreach messages rather than from the threads.
    A thread's own property_key is only its latest property, so an owner texted
    about house A and then house B would leave A looking free, and could be
    texted about A again — and a rival could claim it out from under us.
    Messages are history and never move.

    This is the one query in the system that deliberately reads across the
    tenant boundary. It uses the contact internally to compare the pair, but
    returns only a blocking reason — never a brokerage, account, owner, or
    number belonging to another tenant.
    """
    wanted = {key for lead in leads if (key := _recipient_key(lead)) is not None}
    if not wanted:
        return NO_CLAIMS

    property_keys = sorted({property_key for property_key, _ in wanted})
    rows = []
    for property_batch in _batches(property_keys):
        rows.extend(
            session.query(Message.property_key, Conversation.contact,
                          Conversation.user_id.in_(scope))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Message.property_key.in_(property_batch),
                    Message.event_type == 'outreach.initial')
            .distinct()
            .all()
        )
        # A durable queued submission owns the pair before its Message exists.
        # This closes the interval where two brokerages could enqueue the same
        # property/contact while the worker was still waiting for capacity.
        rows.extend(
            session.query(OutboundSmsJob.property_key, OutboundSmsJob.to_number,
                          OutboundSmsJob.user_id.in_(scope))
            .filter(OutboundSmsJob.property_key.in_(property_batch),
                    OutboundSmsJob.status.in_((
                        'awaiting_lookup', 'looking_up', 'queued', 'submitting',
                        'retry_wait', 'accepted', 'uncertain',
                    )))
            .distinct()
            .all()
        )
    normalized_rows = [
        ((property_key, normalize_phone(contact or '')), is_ours)
        for property_key, contact, is_ours in rows
    ]
    ours = {key for key, is_ours in normalized_rows if key in wanted and is_ours}
    theirs = {key for key, is_ours in normalized_rows if key in wanted and not is_ours}
    # A property/contact pair both worked is reported as ours: it is actionable.
    return Claims(ours, theirs - ours)


def owned(session: Session, user: User, campaign_id: int) -> Campaign:
    campaign = (session.query(Campaign)
                .filter(Campaign.id == campaign_id,
                        Campaign.user_id.in_(brokerage_user_ids(session, user)))
                .first())
    if campaign is None:
        raise LookupError('Campaign not found.')
    return campaign


def _default_name(session: Session, user: User) -> str:
    count = (session.query(Campaign)
             .filter(Campaign.user_id.in_(brokerage_user_ids(session, user)))
             .count())
    return f'Campaign {count + 1}'


# -- drafting --------------------------------------------------------------


def create_draft(session: Session, user: User, lead_ids: list[int],
                 name: str = '', template: str = '',
                 allow_empty: bool = False,
                 group_mode: bool = False,
                 progress=None) -> tuple[Campaign, list[dict]]:
    """Hold a selection of leads under a new draft. Nothing is sent yet.

    Returns the draft and the leads it could not take, so the pool can say why
    a selection of forty arrived in the composer as thirty-seven.

    `allow_empty` keeps a draft that took nothing. The composer never wants one
    — there would be nothing to write to — so it defaults to raising. The
    one-shot `launch` below passes it, because its callers expect every lead
    accounted for in `skipped` rather than one error for the whole batch.
    """
    unique_lead_ids = list(dict.fromkeys(lead_ids))
    if not unique_lead_ids:
        raise ValueError('Select at least one lead to build a campaign.')

    campaign = Campaign(
        user_id=user.id,
        name=(name or '').strip() or _default_name(session, user),
        message_template=(template or '').strip() or message_template.DEFAULT_TEMPLATE,
        reason_groups=json.dumps({'version': 1, 'groups': {}}) if group_mode else None,
        status='draft',
        created_at=datetime.utcnow(),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    left_behind = attach(
        session, user, campaign, unique_lead_ids, progress=progress,
    )
    if len(left_behind) == len(unique_lead_ids) and not allow_empty:
        # Nothing could be taken, so there is no draft worth composing. Roll the
        # empty shell back rather than dropping the broker into a blank screen.
        session.delete(campaign)
        session.commit()
        reasons = sorted({row['reason'] for row in left_behind})
        raise ValueError('None of the selected leads can start a campaign. '
                         + ' '.join(reasons))
    return campaign, left_behind


def _stored_details(lead: Lead) -> dict:
    try:
        parsed = json.loads(lead.details or '{}')
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _provider_contacts(lead: Lead) -> list[dict]:
    """Contactable provider numbers, retaining the person attached to each."""
    if lead.source != PROVIDER_LEAD_SOURCE:
        return []
    details = _stored_details(lead)
    candidates = details.get('_phone_numbers') or details.get('phones') or []
    contacts: list[dict] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict) or item.get('dnc') in (True, 'true', 'Y', 'yes', 1):
            continue
        number = normalize_phone(str(item.get('phone') or item.get('number') or ''))
        if not number or number in seen:
            continue
        seen.add(number)
        contacts.append({
            **item,
            'phone': number,
            'owner_name': str(item.get('owner_name') or lead.owner_name or ''),
        })
    if not contacts and lead.phone and not lead.dnc:
        contacts.append({'phone': normalize_phone(lead.phone),
                         'owner_name': lead.owner_name or ''})
    return contacts


def _expand_provider_lead(session: Session, lead: Lead, campaign: Campaign) -> None:
    """Represent each allowed phone as one independently deliverable lead row."""
    contacts = _provider_contacts(lead)
    if not contacts:
        return
    details = _stored_details(lead)
    details['_campaign_original'] = {
        'owner_name': lead.owner_name,
        'phone': lead.phone,
        'dnc': bool(lead.dnc),
    }
    first, *remaining = contacts
    lead.owner_name = first['owner_name'] or lead.owner_name
    lead.phone = first['phone']
    lead.dnc = False
    lead.details = json.dumps(details)

    for contact in remaining:
        clone_details = {
            **details,
            '_parent_lead_id': lead.id,
            '_phone_numbers': [{**contact, 'phone': contact['phone']}],
        }
        clone_details.pop('_campaign_original', None)
        clone = Lead(
            user_id=lead.user_id,
            owner_name=contact['owner_name'] or lead.owner_name,
            phone=contact['phone'],
            property_address=lead.property_address,
            area=lead.area,
            latitude=lead.latitude,
            longitude=lead.longitude,
            geocoding_status=lead.geocoding_status,
            geocoding_provider=lead.geocoding_provider,
            geocoded_at=lead.geocoded_at,
            normalized_address=lead.normalized_address,
            signals=lead.signals,
            main_signal=lead.main_signal,
            main_signal_basis=lead.main_signal_basis,
            outreach_reason=lead.outreach_reason,
            details=json.dumps(clone_details),
            score=lead.score,
            source=PROVIDER_CONTACT_SOURCE,
            dnc=False,
            campaign_id=campaign.id,
        )
        session.add(clone)
    session.flush()


def _restore_provider_master(lead: Lead) -> None:
    details = _stored_details(lead)
    original = details.pop('_campaign_original', None)
    if isinstance(original, dict):
        lead.owner_name = original.get('owner_name')
        lead.phone = original.get('phone')
        lead.dnc = bool(original.get('dnc'))
        lead.details = json.dumps(details)


def _save_number_lookup(lead: Lead, result: number_lookup.NumberLookupResult) -> None:
    """Merge verified line data into the existing per-number details contract."""
    metadata = result.saved_metadata()
    if metadata is None:
        return
    details = _stored_details(lead)
    candidates = details.get('_phone_numbers')
    if not isinstance(candidates, list):
        candidates = []
    target = normalize_phone(lead.phone or result.phone_number)
    matched = False
    updated: list = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            updated.append(candidate)
            continue
        item = dict(candidate)
        candidate_phone = normalize_phone(str(item.get('phone') or item.get('number') or ''))
        if candidate_phone == target:
            item['telnyx_lookup'] = metadata
            item['lookup_status'] = result.status
            item['lookup_checked_at'] = result.checked_at
            if result.line_type:
                item['type'] = result.line_type
            if result.carrier_name:
                item['carrier'] = result.carrier_name
            matched = True
        updated.append(item)
    if not matched:
        updated.append({
            'phone': target or result.phone_number,
            'dnc': bool(lead.dnc),
            'owner_name': lead.owner_name or '',
            'type': result.line_type,
            'carrier': result.carrier_name,
            'lookup_status': result.status,
            'lookup_checked_at': result.checked_at,
            'telnyx_lookup': metadata,
        })
    details['_phone_numbers'] = updated
    lead.details = json.dumps(details)


def _lookup_skip_reason(result: number_lookup.NumberLookupResult) -> str | None:
    if not result.enforces_mobile_gate or result.is_mobile:
        return None
    if result.status == 'verified' and result.line_type:
        label = result.line_type.replace('_', ' ').replace('-', ' ')
        return f'Number is {label}; campaigns send only to mobile numbers.'
    if result.status == 'unknown':
        return 'Number type could not be verified as mobile; no message was sent.'
    return 'Number lookup failed, so no message was sent.'


def release_draft_members(session: Session, user: User, campaign: Campaign) -> None:
    """Return real leads to the pool and discard generated contact rows."""
    for lead in members(session, user, campaign):
        if lead.source == PROVIDER_CONTACT_SOURCE:
            session.delete(lead)
            continue
        if lead.source == PROVIDER_LEAD_SOURCE:
            _restore_provider_master(lead)
        lead.campaign_id = None
        lead_events.log_event(
            session, lead.id, lead_events.STAGE, 'released_from_campaign',
            actor_type='broker', actor_id=user.id,
            from_value=campaign.name, meta={'campaign_id': campaign.id}, commit=False,
        )


def attach(session: Session, user: User, campaign: Campaign, lead_ids: list[int],
           progress=None) -> list[dict]:
    """Move the broker's own leads into this draft. Returns the ones left behind.

    A lead belongs to one campaign and one only. Two rules enforce it, and both
    matter for a different reason:

    - A lead already handed to Bobbie keeps the campaign that texted it. Its
      history belongs there, and moving it would silently rewrite that
      campaign's delivered/replied numbers.
    - A lead sitting in another *draft* stays there too. Otherwise building a
      second draft over an overlapping selection would quietly empty the first,
      and the broker would find out when it sent to fewer owners than it showed.

    A lead is only ever freed by discarding the draft holding it, which is an
    explicit act. Ownership is taken from the JWT, so a lead in another
    brokerage's pool is not visible here at all and reads as "not found".
    """
    if campaign.status != 'draft':
        raise ValueError('This campaign has already been sent, so its leads are fixed.')

    lead_ids = list(dict.fromkeys(lead_ids))
    total = len(lead_ids)
    if progress:
        progress(total, 0)

    scope = brokerage_user_ids(session, user)
    found: dict[int, Lead] = {}
    from ..integration_data.service import backfill_distributed_leads
    for lead_id_batch in _batches(lead_ids):
        batch = (session.query(Lead)
                 .filter(Lead.user_id.in_(scope), Lead.id.in_(lead_id_batch))
                 .all())
        found.update({lead.id: lead for lead in batch})
    # The backfill may rebuild combined provider data, so invoke it once for
    # the complete selection rather than repeating that work for every batch.
    backfill_distributed_leads(session, list(found.values()))
    # Naming the campaign that holds a lead is more useful than "already taken".
    held_by = {row[0]: row[1] for row in session.query(Campaign.id, Campaign.name)
               .filter(Campaign.user_id.in_(scope)).all()}

    left_behind: list[dict] = []
    processed = 0
    selected_pairs: set[tuple[str, str]] = set()
    for lead_id_batch in _batches(lead_ids):
        batch_leads = [found[lead_id] for lead_id in lead_id_batch if lead_id in found]
        claims = claims_on(session, scope, batch_leads)
        eligible: list[int] = []
        for lead_id in lead_id_batch:
            lead = found.get(lead_id)
            if lead is None:
                left_behind.append({
                    'lead_id': lead_id, 'owner_name': None, 'reason': 'Lead not found.',
                })
                continue
            reason = selection_reason(lead)
            if reason:
                # Not ready: it stays in the pool, where the broker can see the
                # stage and act on it. A lead held by a campaign is hidden from
                # the pool, so one that can never be sent would be hidden with
                # no way to fix it.
                left_behind.append({
                    'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': reason,
                })
            elif claims.reason_for(lead) and lead.source != PROVIDER_LEAD_SOURCE:
                left_behind.append({
                    'lead_id': lead.id, 'owner_name': lead.owner_name,
                    'reason': claims.reason_for(lead),
                })
            elif lead.campaign_id is not None and lead.campaign_id != campaign.id:
                name = held_by.get(lead.campaign_id, 'another campaign')
                left_behind.append({
                    'lead_id': lead.id, 'owner_name': lead.owner_name,
                    'reason': f'Already in the draft “{name}”.',
                })
            else:
                pair = _recipient_key(lead)
                if pair is not None and pair in selected_pairs:
                    left_behind.append({
                        'lead_id': lead.id, 'owner_name': lead.owner_name,
                        'reason': 'Duplicate property and phone in this selection.',
                    })
                else:
                    signals = split_signals(lead)
                    if lead.main_signal is None and len(signals) == 1:
                        lead.main_signal = signals[0]
                        lead.main_signal_basis = 'single_signal'
                    if pair is not None:
                        selected_pairs.add(pair)
                    eligible.append(lead.id)

        if eligible:
            # The guarded update re-checks the lock invariant at write time.
            session.query(Lead).filter(
                Lead.user_id.in_(scope),
                Lead.id.in_(eligible),
                Lead.campaign_id.is_(None),
                Lead.conversation_id.is_(None),
            ).update({Lead.campaign_id: campaign.id}, synchronize_session=False)
            session.commit()
            session.expire_all()

            claimed = {row[0] for row in session.query(Lead.id)
                       .filter(Lead.id.in_(eligible),
                               Lead.campaign_id == campaign.id).all()}
            for lead_id in eligible:
                if lead_id not in claimed:
                    lead = found[lead_id]
                    left_behind.append({
                        'lead_id': lead_id, 'owner_name': lead.owner_name,
                        'reason': 'Just claimed by another campaign.',
                    })
            for lead_id in eligible:
                if lead_id not in claimed:
                    continue
                # Record one history row per lock, but commit once per batch.
                lead_events.log_event(
                    session, lead_id, lead_events.STAGE, 'locked_to_campaign',
                    actor_type='broker', actor_id=user.id,
                    to_value=campaign.name, meta={'campaign_id': campaign.id},
                    commit=False,
                )
                lead = session.get(Lead, lead_id)
                if lead and lead.source == PROVIDER_LEAD_SOURCE:
                    _expand_provider_lead(session, lead, campaign)
            session.commit()

        processed += len(lead_id_batch)
        if progress:
            progress(total, processed)

    # Preserve any provider backfill even when every selected row was refused.
    session.commit()
    return left_behind


def update_draft(session: Session, user: User, campaign: Campaign,
                 name: str | None = None, template: str | None = None,
                 reason: str | None = None,
                 reason_group: dict | None = None) -> Campaign:
    """Rename a campaign, rewrite its message or its reason. Drafts only."""
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError('Give the campaign a name.')
        campaign.name = cleaned[:200]
    if template is not None:
        if campaign.status != 'draft':
            raise ValueError('This campaign has already been sent; its message cannot change.')
        message_template.validate(template)
        campaign.message_template = template.strip()
    if reason is not None:
        if campaign.status != 'draft':
            raise ValueError('This campaign has already been sent; its reason cannot change.')
        # Blank is meaningful: it hands {{reason}} back to each lead's own signal.
        campaign.outreach_reason = reason.strip()[:300] or None
    if reason_group is not None:
        _update_reason_group(campaign, reason_group)
    session.commit()
    session.refresh(campaign)
    return campaign


def _members_query(session: Session, user: User, campaign: Campaign):
    return session.query(Lead).filter(
        Lead.user_id.in_(brokerage_user_ids(session, user)),
        Lead.campaign_id == campaign.id,
    )


def _filtered_members_query(session: Session, user: User, campaign: Campaign,
                            filters: dict | None = None):
    query = _members_query(session, user, campaign)
    filters = filters or {}
    wanted = list(dict.fromkeys(filters.get('signals') or []))
    invalid = [key for key in wanted if key not in SIGNALS]
    if invalid:
        raise ValueError('Choose only valid campaign signals.')
    for key in wanted:
        query = query.filter(
            (',' + func.coalesce(Lead.signals, '') + ',').like(f'%,{key},%')
        )
    if filters.get('no_signal'):
        padded = ',' + func.coalesce(Lead.signals, '') + ','
        for key in SIGNALS:
            query = query.filter(~padded.like(f'%,{key},%'))
    main_signal = str(filters.get('main_signal') or '').strip()
    if main_signal:
        if main_signal not in SIGNALS:
            raise ValueError('Choose a valid Main Signal.')
        query = query.filter(Lead.main_signal == main_signal)
    area = str(filters.get('area') or '').strip()
    if area:
        pattern = f'%{area}%'
        query = query.filter(or_(Lead.area.ilike(pattern), Lead.property_address.ilike(pattern)))
    search = str(filters.get('search') or '').strip()
    if search:
        pattern = f'%{search}%'
        query = query.filter(or_(Lead.owner_name.ilike(pattern),
                                 Lead.property_address.ilike(pattern)))
    return query


def member_count(session: Session, user: User, campaign: Campaign) -> int:
    return _members_query(session, user, campaign).count()


def members(session: Session, user: User, campaign: Campaign,
            offset: int = 0, limit: int | None = None,
            filters: dict | None = None) -> list[Lead]:
    query = _filtered_members_query(
        session, user, campaign, filters,
    ).order_by(Lead.score.desc(), Lead.id)
    if offset:
        query = query.offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def reason_group_summary(session: Session, user: User,
                         campaign: Campaign,
                         context: dict | None = None) -> tuple[list[dict], int, int, list[dict]]:
    """One property-level pass for valid signal rows and reason-review safety."""
    context = context or _campaign_reason_context(session, user, campaign)
    counts = dict(context['frequencies'])
    no_signal_count = sum(
        not signals for signals in context['property_signals'].values())
    if no_signal_count:
        counts[NO_SIGNAL_REASON_KEY] = no_signal_count
    state = context['state']
    stored = state['groups'] if state else {}
    needs_review = 0
    unresolved = 0
    if state is not None:
        for key, family in context['properties'].items():
            reason_signal = context['assignments'][key]
            if len(context['property_signals'][key]) > 1 and reason_signal is None:
                unresolved += 1
            if any(resolve_reason(
                    campaign, row, reason_signal,
                    state=state, reason_required=context['reason_required'],
            )[2] for row in family):
                needs_review += 1

    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    groups = []
    for key, count in ordered:
        saved = stored.get(key) if isinstance(stored.get(key), dict) else {}
        groups.append({
            'key': key,
            'label': 'No Signal' if key == NO_SIGNAL_REASON_KEY else signal_label(key),
            'count': count,
            'reason': str(saved.get('reason') or ''),
            'confirmed': bool(saved.get('confirmed')),
        })
    signal_options = [
        {'key': key, 'label': signal_label(key)} for key, _count in ordered
        if key != NO_SIGNAL_REASON_KEY
    ]
    return groups, unresolved, needs_review, signal_options


def campaign_signal_options(session: Session, user: User, campaign: Campaign) -> list[dict]:
    """Every valid multi-signal category represented in this campaign."""
    found: set[str] = set()
    for (stored,) in (_members_query(session, user, campaign)
                      .with_entities(Lead.signals).distinct().all()):
        for key in str(stored or '').split(','):
            if key in SIGNALS:
                found.add(key)
    return [
        {'key': key, 'label': signal_label(key)}
        for key in SIGNALS if key in found
    ]


def member_ids(session: Session, user: User, campaign: Campaign) -> list[int]:
    """Stable full recipient identity; previews never define membership."""
    return [row[0] for row in _members_query(session, user, campaign)
            .order_by(Lead.score.desc(), Lead.id)
            .with_entities(Lead.id)
            .all()]


def members_by_ids(session: Session, user: User, campaign: Campaign,
                   lead_ids: list[int]) -> list[Lead]:
    """Load one bounded part of an authoritative campaign recipient set."""
    if not lead_ids:
        return []
    by_id = {lead.id: lead for lead in _members_query(session, user, campaign)
             .filter(Lead.id.in_(lead_ids)).all()}
    return [by_id[lead_id] for lead_id in lead_ids if lead_id in by_id]


def _locked_draft(session: Session, user: User, campaign: Campaign) -> Campaign:
    locked = (session.query(Campaign)
              .filter(Campaign.id == campaign.id,
                      Campaign.user_id.in_(brokerage_user_ids(session, user)))
              .with_for_update()
              .one())
    if locked.status != 'draft':
        raise ValueError('Only a draft campaign can be changed.')
    return locked


def _selected_members(session: Session, user: User, campaign: Campaign,
                      selection: dict) -> list[Lead]:
    mode = selection.get('mode')
    if mode == 'ids':
        ids = list(dict.fromkeys(selection.get('lead_ids') or []))
        if not ids:
            raise ValueError('Select at least one campaign contact.')
        rows = (_members_query(session, user, campaign)
                .filter(Lead.id.in_(ids)).with_for_update().all())
        if len(rows) != len(ids):
            raise ValueError('One or more selected contacts are no longer in this draft.')
        return rows
    if mode == 'filtered':
        rows = (_filtered_members_query(
            session, user, campaign, selection.get('filters') or {},
        ).with_for_update().all())
        if not rows:
            raise ValueError('No campaign contacts match these filters.')
        return rows
    raise ValueError('Choose selected contacts or all filtered contacts.')


def assign_main_signal(session: Session, user: User, campaign: Campaign,
                       main_signal: str, selection: dict) -> dict:
    """Assign one valid signal, expanding provider contacts to their property family."""
    campaign = _locked_draft(session, user, campaign)
    if main_signal not in SIGNALS:
        raise ValueError('Choose a valid Main Signal.')
    selected = _selected_members(session, user, campaign, selection)
    family_ids: set[int] = set()
    ordinary_ids: set[int] = set()
    for lead in selected:
        if lead.source == PROVIDER_LEAD_SOURCE:
            family_ids.add(lead.id)
        elif lead.source == PROVIDER_CONTACT_SOURCE:
            parent = _stored_details(lead).get('_parent_lead_id')
            if not isinstance(parent, int):
                raise ValueError('A provider contact is missing its property relationship.')
            family_ids.add(parent)
        else:
            ordinary_ids.add(lead.id)

    candidates = (_members_query(session, user, campaign)
                  .filter(Lead.id.in_(ordinary_ids | family_ids))
                  .with_for_update().all())
    if family_ids:
        candidates.extend(
            _members_query(session, user, campaign)
            .filter(Lead.source == PROVIDER_CONTACT_SOURCE)
            .with_for_update().all()
        )
    targets = []
    for lead in candidates:
        if lead.id in ordinary_ids or lead.id in family_ids:
            targets.append(lead)
            continue
        parent = _stored_details(lead).get('_parent_lead_id')
        if parent in family_ids:
            targets.append(lead)

    for lead in targets:
        if main_signal not in split_signals(lead):
            raise ValueError('The Main Signal must already belong to every selected property.')
        if (lead.main_signal_basis in {'single_signal', 'source_verified'}
                and lead.main_signal != main_signal):
            raise ValueError('Historically verified Main Signals cannot be overwritten here.')

    for lead in targets:
        if lead.main_signal_basis not in {'single_signal', 'source_verified'}:
            lead.main_signal = main_signal
            lead.main_signal_basis = 'broker_selected'
    session.commit()
    return {
        'updated_contacts': len(targets),
        'updated_properties': len(ordinary_ids) + len(family_ids),
    }


def _merge_contact_metadata(master: Lead, contact: Lead) -> None:
    """Preserve richer per-phone metadata before a generated row is removed."""
    master_details = _stored_details(master)
    contact_details = _stored_details(contact)
    existing = master_details.get('_phone_numbers')
    if not isinstance(existing, list):
        existing = []
    by_phone = {
        normalize_phone(str(item.get('phone') or item.get('number') or '')): dict(item)
        for item in existing if isinstance(item, dict)
    }
    for item in contact_details.get('_phone_numbers') or []:
        if not isinstance(item, dict):
            continue
        phone = normalize_phone(str(item.get('phone') or item.get('number') or ''))
        if phone:
            by_phone[phone] = {**by_phone.get(phone, {}), **item, 'phone': phone}
    master_details['_phone_numbers'] = list(by_phone.values())
    master.details = json.dumps(master_details)


def _delete_generated_contact(session: Session, master: Lead, contact: Lead) -> None:
    _merge_contact_metadata(master, contact)
    session.query(LeadEvent).filter(LeadEvent.lead_id == contact.id).update(
        {LeadEvent.lead_id: master.id}, synchronize_session=False,
    )
    session.delete(contact)


def remove_recipients(session: Session, user: User, campaign: Campaign,
                      selection: dict) -> dict:
    """Remove selected phone recipients while retaining each provider property."""
    campaign = _locked_draft(session, user, campaign)
    if selection.get('mode') == 'unresolved':
        context = _finalize_reason_signal_snapshot(session, user, campaign)
        unresolved_ids = [
            row.id for row in context['leads']
            if resolve_reason(
                campaign, row,
                context['assignments'].get(
                    _property_key_values(row.id, row.property_address)),
                state=context['state'],
                reason_required=context['reason_required'],
            )[2]
        ]
        if not unresolved_ids:
            raise ValueError('No campaign recipients still need reason review.')
        selected = (_members_query(session, user, campaign)
                    .filter(Lead.id.in_(unresolved_ids)).with_for_update().all())
    else:
        selected = _selected_members(session, user, campaign, selection)
    selected_ids = {lead.id for lead in selected}
    if session.query(OutboundSmsJob.id).filter(
            OutboundSmsJob.campaign_id == campaign.id).first():
        raise ValueError('Queued campaign contacts cannot be removed.')
    if _members_query(session, user, campaign).filter(
            Lead.conversation_id.isnot(None)).first():
        raise ValueError('A contact with conversation history cannot be removed from a draft.')

    all_members = (_members_query(session, user, campaign)
                   .with_for_update().all())
    masters = {lead.id: lead for lead in all_members if lead.source == PROVIDER_LEAD_SOURCE}
    clones_by_parent: dict[int, list[Lead]] = {}
    for lead in all_members:
        if lead.source != PROVIDER_CONTACT_SOURCE:
            continue
        parent = _stored_details(lead).get('_parent_lead_id')
        if isinstance(parent, int):
            clones_by_parent.setdefault(parent, []).append(lead)

    removed_contacts = 0
    returned_properties = 0
    handled: set[int] = set()
    for master_id, master in masters.items():
        family = [master, *clones_by_parent.get(master_id, [])]
        family_selected = [lead for lead in family if lead.id in selected_ids]
        if not family_selected:
            continue
        handled.update(lead.id for lead in family_selected)
        remaining = [lead for lead in family if lead.id not in selected_ids]
        for clone in [lead for lead in family_selected if lead is not master]:
            _delete_generated_contact(session, master, clone)
            removed_contacts += 1
        if master.id not in selected_ids:
            continue
        removed_contacts += 1
        if not remaining:
            _restore_provider_master(master)
            master.campaign_id = None
            returned_properties += 1
            lead_events.log_event(
                session, master.id, lead_events.STAGE, 'released_from_campaign',
                actor_type='broker', actor_id=user.id, from_value=campaign.name,
                meta={'campaign_id': campaign.id}, commit=False,
            )
            continue
        promoted = min(remaining, key=lambda lead: lead.id)
        if promoted is not master:
            _merge_contact_metadata(master, promoted)
            master.owner_name = promoted.owner_name
            master.phone = promoted.phone
            master.dnc = promoted.dnc
            master.main_signal = promoted.main_signal
            master.main_signal_basis = promoted.main_signal_basis
            _delete_generated_contact(session, master, promoted)

    for lead in selected:
        if lead.id in handled:
            continue
        if lead.source == PROVIDER_CONTACT_SOURCE:
            raise ValueError('A provider contact is missing its original property.')
        lead.campaign_id = None
        removed_contacts += 1
        returned_properties += 1
        lead_events.log_event(
            session, lead.id, lead_events.STAGE, 'released_from_campaign',
            actor_type='broker', actor_id=user.id, from_value=campaign.name,
            meta={'campaign_id': campaign.id}, commit=False,
        )
    session.commit()
    return {
        'removed_contacts': removed_contacts,
        'returned_properties': returned_properties,
        'remaining_contacts': member_count(session, user, campaign),
    }


def _row(lead: Lead, text: str, **extra) -> dict:
    """One line of the campaign's lead table.

    Built from the pool's own serializer so the two tables show the same lead
    the same way — the campaign is where these leads live now.
    """
    row = dict(serialize(lead))
    row.update({
        'lead_id': lead.id,
        'text': text,
        'is_long': len(text) > message_template.LONG_MESSAGE_CHARS,
        'replied': False,
    })
    row.update(extra)
    return row


def _sent_view(session: Session, user: User, campaign: Campaign,
               people: list[Lead]) -> tuple[list[dict], list[dict]]:
    """What a sent campaign actually sent, read back from the messages.

    Re-rendering the template would be a guess: the template may have been
    edited, and the lead's own reason may since have changed. The stored message
    is what the owner received, so that is what the campaign's record shows.
    """
    conversation_ids = [lead.conversation_id for lead in people if lead.conversation_id]

    outreach = []
    if conversation_ids:
        outreach = (session.query(Message)
                    .filter(Message.campaign_id == campaign.id,
                            Message.event_type == 'outreach.initial',
                            Message.conversation_id.in_(conversation_ids))
                    .all())
    # Keyed by property as well as thread: one campaign can text the same owner
    # about two of their listings, which is two messages on one thread.
    by_property = {(row.conversation_id, row.property_key): row for row in outreach}

    # A recipient rejected before provider submission has no Message row, but
    # its durable job records the authoritative reason (for example, Telnyx
    # verified that the destination is a landline). Keep that reason in the
    # completed-campaign view instead of reducing it to "Was not sent."
    lead_ids = [lead.id for lead in people]
    job_reasons = dict(
        session.query(OutboundSmsJob.lead_id, OutboundSmsJob.last_error)
        .filter(
            OutboundSmsJob.campaign_id == campaign.id,
            OutboundSmsJob.lead_id.in_(lead_ids),
            OutboundSmsJob.last_error.isnot(None),
        )
        .all()
    ) if lead_ids else {}

    latest_reply: dict[int, datetime] = {}
    if conversation_ids:
        latest_reply = dict(session.query(Message.conversation_id,
                                          func.max(Message.created_at))
                            .filter(Message.conversation_id.in_(conversation_ids),
                                    Message.direction == 'inbound')
                            .group_by(Message.conversation_id)
                            .all())

    sent_rows: list[dict] = []
    missed_rows: list[dict] = []
    for lead in people:
        key = address_key.canonical(lead.property_address)
        message = by_property.get((lead.conversation_id, key))
        if message is None:
            # No outreach carries this lead's property, so it never went out.
            # Prefer the reason recorded at send time; current lead state is
            # only a fallback for older campaigns without durable jobs.
            standing = _blocking_reason_for_record(lead)
            missed_rows.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': job_reasons.get(lead.id) or standing or 'Was not sent.'})
            continue
        answered = latest_reply.get(lead.conversation_id)
        sent_rows.append(_row(
            lead, message.text or '',
            conversation_id=lead.conversation_id,
            replied=bool(answered and answered >= message.created_at),
            delivery_status=message.status,
            delivery_failure_reason=message.failure_reason,
        ))
    return sent_rows, missed_rows


def _blocking_reason_for_record(lead: Lead) -> str | None:
    """Why a lead could not be texted, ignoring the thread it may now have."""
    if lead.dnc:
        return 'On the do-not-contact list.'
    if not lead.phone:
        return 'No usable phone number.'
    if not lead.property_address:
        return 'No property address for Bobbie to reference.'
    return None


# A lead is campaignable once it is contactable, which is exactly what "Ready
# for Outreach" means. Every other stage is refused with a reason a broker can
# act on.
STAGE_REFUSAL: dict[str, str] = {
    'dnc': 'On the do-not-contact list.',
    'in_campaign': 'Already texted in a campaign.',
    'needs_review': 'Needs review — no usable phone number.',
}


def selection_reason(lead: Lead) -> str | None:
    """Why this lead cannot join a campaign, or None when it can.

    The stage is the gate: a broker selecting a whole filtered page should get
    the ones that can actually be texted, not a campaign quietly padded with
    leads that are on the DNC list or have no number.
    """
    stage = derive_stage(lead)
    if stage != 'ready':
        return STAGE_REFUSAL.get(stage, 'Not ready for outreach.')
    if not lead.property_address:
        return 'No property address for Bobbie to reference.'
    return None


def preview(session: Session, user: User, campaign: Campaign,
            page: int = 1, page_size: int = 20,
            filters: dict | None = None) -> dict:
    """The campaign's leads: what each would read, or what each actually read.

    Only the requested page is rendered. Campaign membership remains complete,
    while the composer loads each 20-recipient window as it is viewed.
    """
    page_size = max(1, min(page_size, 100))
    total_items = _filtered_members_query(session, user, campaign, filters).count()
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    current_page = min(max(page, 1), total_pages)
    people = members(
        session, user, campaign,
        offset=(current_page - 1) * page_size,
        limit=page_size,
        filters=filters,
    )
    group_state = _group_state(campaign)
    reason_context = (_campaign_reason_context(
        session, user, campaign, state=group_state,
    ) if group_state is not None else None)
    groups, unresolved_count, needs_review_count, signal_options = reason_group_summary(
        session, user, campaign, reason_context,
    )

    if campaign.status == 'sent':
        sent_rows, missed_rows = _sent_view(session, user, campaign, people)
        return {
            'campaign_id': campaign.id,
            'recipients': sent_rows,
            'skipped': missed_rows,
            'page': current_page,
            'page_size': page_size,
            'total_items': total_items,
            'total_pages': total_pages,
            'unresolved_main_signal_count': unresolved_count,
            'needs_review_count': needs_review_count,
            'reason_groups': groups,
            'signal_options': signal_options,
            'template_error': '',
            'tokens': message_template.TOKEN_HELP,
        }

    if campaign.status == 'sending':
        jobs = (session.query(OutboundSmsJob)
                .filter(OutboundSmsJob.campaign_id == campaign.id,
                        OutboundSmsJob.lead_id.in_([lead.id for lead in people]))
                .all()) if people else []
        by_lead = {job.lead_id: job for job in jobs}
        rendered = []
        skipped = []
        for lead in people:
            job = by_lead.get(lead.id)
            if job is None:
                continue
            if job.status == 'skipped':
                skipped.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': job.last_error or 'Was not queued.'})
            else:
                rendered.append(_row(lead, job.text))
        return {
            'campaign_id': campaign.id,
            'recipients': rendered,
            'skipped': skipped,
            'page': current_page,
            'page_size': page_size,
            'total_items': total_items,
            'total_pages': total_pages,
            'unresolved_main_signal_count': unresolved_count,
            'needs_review_count': needs_review_count,
            'reason_groups': groups,
            'signal_options': signal_options,
            'template_error': '',
            'tokens': message_template.TOKEN_HELP,
        }

    rendered: list[dict] = []
    skipped: list[dict] = []
    from ..integration_data.service import backfill_distributed_leads
    if backfill_distributed_leads(session, people):
        session.commit()
        people = members(
            session, user, campaign,
            offset=(current_page - 1) * page_size,
            limit=page_size,
            filters=filters,
        )
    claims = claims_on(session, brokerage_user_ids(session, user), people)
    template = campaign.message_template or ''
    error = ''
    try:
        message_template.validate(template)
    except ValueError as problem:
        error = str(problem)

    for lead in people:
        blocked = _blocking_reason(lead, claims)
        if blocked:
            skipped.append({'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': blocked})
            continue
        reason_signal = (reason_context['assignments'].get(_property_key(lead))
                         if reason_context else _REASON_SIGNAL_UNSET)
        reason, reason_source, needs_review = resolve_reason(
            campaign, lead, reason_signal,
            state=reason_context['state'] if reason_context else _REASON_SIGNAL_UNSET,
            reason_required=(reason_context['reason_required']
                             if reason_context else None),
        )
        if needs_review:
            rendered.append(_row(
                lead, '', reason_source=reason_source, needs_review=True,
            ))
            continue
        text = '' if error else message_template.render(
            template, lead, user, reason)
        rendered.append(_row(
            lead, text, reason_source=reason_source, needs_review=needs_review,
        ))

    return {
        'campaign_id': campaign.id,
        'recipients': rendered,
        'skipped': skipped,
        'page': current_page,
        'page_size': page_size,
        'total_items': total_items,
        'total_pages': total_pages,
        'unresolved_main_signal_count': unresolved_count,
        'needs_review_count': needs_review_count,
        'reason_groups': groups,
        'signal_options': signal_options,
        'template_error': error,
        'tokens': message_template.TOKEN_HELP,
    }


# -- sending ---------------------------------------------------------------


def _previous_properties(conversation: Conversation) -> list[str]:
    """Properties this thread has already covered, from its stored context."""
    try:
        stored = json.loads(conversation.lead_context or '{}')
    except ValueError:
        return []
    earlier = stored.get('other_properties')
    return [item for item in earlier if isinstance(item, str)] if isinstance(earlier, list) else []


def _send_to_lead(session: Session, user: User, campaign: Campaign, lead: Lead,
                  text: str) -> tuple[dict | None, dict | None]:
    """Open or continue the owner's thread and send one rendered message.

    An owner gets exactly one thread, whatever they own. An inbound SMS carries
    no thread identifier — the carrier gives us a from-number and nothing else,
    and app.routes.webhooks can only route a reply to the newest thread for that
    number. A second thread for a second property would therefore silently
    swallow the replies meant for the first, and Bobbie would answer about the
    wrong house. So a landlord's second property is a second message in the
    same conversation, and the thread's grounding moves to that property.
    """
    conversation, key = _prepare_conversation(session, user, campaign, lead)
    try:
        message = sms_service.send_and_store_message(
            session, conversation, text, 'outreach.initial')
    except sms_service.SmsDeliveryError as error:
        return None, {'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': str(error)}
    return _record_sent(session, user, campaign, lead, conversation, message, key, text), None


def _prepare_conversation(session: Session, user: User, campaign: Campaign,
                          lead: Lead) -> tuple[Conversation, str]:
    """Prepare the existing owner thread without submitting an SMS."""
    key = address_key.canonical(lead.property_address)
    conversation = (session.query(Conversation)
                    .filter(Conversation.contact == lead.phone,
                            Conversation.user_id.in_(brokerage_user_ids(session, user)))
                    .first())
    already_spoken = conversation is not None and session.query(Message).filter(
        Message.conversation_id == conversation.id).count() > 0

    # Ground Bobbie in the same reason the owner was just texted, not a
    # different one derived from the lead's signals.
    state = _group_state(campaign)
    reason_required = _reason_is_required(campaign)
    reason_signal = (_snapshot_reason_signal(campaign, lead, state)
                     if state is not None else _REASON_SIGNAL_UNSET)
    why, _source, needs_review = resolve_reason(
        campaign, lead, reason_signal,
        state=state, reason_required=reason_required,
    )
    if needs_review:
        raise ValueError('This recipient still needs a grounded outreach reason.')
    conversation = conversation or Conversation(
        contact=lead.phone, user_id=user.id, created_at=datetime.utcnow())
    conversation.name = lead.owner_name
    # The thread now refers to the property just written about. The previous one
    # stays in the transcript, and is carried below so Bobbie can still place it
    # if the owner answers about it instead.
    previous_address = conversation.property_address if already_spoken else None
    conversation.property_address = lead.property_address
    conversation.property_key = key
    conversation.campaign_id = campaign.id
    context = build_single_lead_context(lead.property_address, why)
    context['lead_source'] = 'Lead pool campaign'
    context['signals'] = split_signals(lead)
    # Imported attributes are approved facts, so Bobbie may cite them.
    context['property_details'] = conversation_property_details(lead)
    if previous_address and previous_address != lead.property_address:
        # An owner with more than one listing: both are approved facts, and
        # Bobbie needs the earlier one to make sense of "the other one".
        earlier = _previous_properties(conversation)
        earlier.append(previous_address)
        context['other_properties'] = earlier
    conversation.lead_context = json.dumps(context)
    # Campaign outreach starts AI-handled, like a single new conversation. An
    # owner who opted out stays off; a person can still take over any thread.
    opted_out = bool(conversation.dnc_alert) or conversation.lead_status == 'dnc'
    conversation.ai_enabled = not opted_out
    conversation.recipient_ai_enabled = False
    conversation.handled_by = 'broker' if opted_out else 'bobbie'
    conversation.lead_status = 'processing'
    conversation.queue_status = 'idle'
    conversation.processed_at = None
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation, key


def _record_sent(session: Session, user: User, campaign: Campaign, lead: Lead,
                 conversation: Conversation, message: Message, key: str,
                 text: str) -> dict:
    """Apply the existing post-acceptance campaign side effects exactly once."""
    # Attribution lives on the message, because the thread may end up carrying
    # outreach from more than one campaign and more than one property.
    message.campaign_id = campaign.id
    message.property_key = key

    lead.conversation_id = conversation.id
    lead.last_activity_at = datetime.utcnow()
    session.commit()
    lead_events.log_event(
        session, lead.id, lead_events.STAGE, 'attached_to_campaign',
        actor_type='broker', actor_id=user.id,
        to_value=campaign.name, meta={'campaign_id': campaign.id},
    )
    lead_events.log_event(
        session, lead.id, lead_events.ACTIVITY, 'outreach_sent',
        actor_type='broker', actor_id=user.id,
        to_value=campaign.name, meta={'campaign_id': campaign.id, 'message_id': message.id},
    )
    schedule_initial_followup(session, conversation)
    return {
        'lead_id': lead.id,
        'owner_name': lead.owner_name,
        'conversation_id': conversation.id,
        'text': text,
    }


def send(session: Session, user: User, campaign: Campaign) -> dict:
    """Validate and durably enqueue the complete campaign recipient set."""
    campaign = (session.query(Campaign)
                .filter(Campaign.id == campaign.id,
                        Campaign.user_id.in_(brokerage_user_ids(session, user)))
                .with_for_update()
                .one())
    if campaign.status != 'draft':
        raise ValueError('This campaign has already been sent.')
    message_template.validate(campaign.message_template)

    skipped: list[dict] = []

    scope = brokerage_user_ids(session, user)
    from ..integration_data.service import backfill_distributed_leads
    provider_people = (_members_query(session, user, campaign)
                       .filter(Lead.source.in_((
                           PROVIDER_LEAD_SOURCE, PROVIDER_CONTACT_SOURCE,
                       ))).all())
    for batch in _batches(provider_people):
        if backfill_distributed_leads(session, batch):
            session.flush()
    reason_context = _finalize_reason_signal_snapshot(session, user, campaign)
    # This ordered ID snapshot is the authoritative set for the whole run.
    # Preview pagination never enters the send path, and membership changes
    # during processing cannot duplicate or silently omit a recipient.
    recipient_ids = member_ids(session, user, campaign)
    if not recipient_ids:
        raise ValueError('This draft has no recipients. Add leads or discard it.')
    validated_pairs: set[tuple[str, str]] = set()
    jobs: list[OutboundSmsJob] = []
    for recipient_batch in _batches(recipient_ids):
        people = members_by_ids(session, user, campaign, recipient_batch)
        claims = claims_on(session, scope, people)
        for lead in people:
            blocked = _blocking_reason(lead, claims)
            pair = _recipient_key(lead)
            if blocked is None and pair is not None and pair in validated_pairs:
                blocked = 'Duplicate property and phone in this campaign.'
            if blocked:
                skipped.append({
                    'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': blocked,
                })
                status = 'skipped'
                text = ''
                error = blocked
            else:
                reason, _reason_source, needs_review = resolve_reason(
                    campaign, lead,
                    reason_context['assignments'].get(_property_key(lead)),
                    state=reason_context['state'],
                    reason_required=reason_context['reason_required'],
                )
                if needs_review:
                    raise ValueError(
                        'Resolve every Needs Review recipient before sending this campaign.'
                    )
                if pair is not None:
                    validated_pairs.add(pair)
                text = message_template.render(
                    campaign.message_template, lead, user, reason)
                # Lookup stays a separate, sequential worker stage for now.
                status = ('awaiting_lookup'
                          if str(settings['telnyx'].get('mode')).lower() == 'telnyx'
                          else 'queued')
                error = None
            jobs.append(OutboundSmsJob(
                job_key=f'campaign:{campaign.id}:lead:{lead.id}',
                campaign_id=campaign.id,
                lead_id=lead.id,
                user_id=user.id,
                to_number=normalize_phone(lead.phone or ''),
                property_key=address_key.canonical(lead.property_address),
                text=text,
                status=status,
                last_error=error,
            ))

    session.add_all(jobs)
    campaign.status = 'sending'
    session.commit()
    return {
        'campaign_id': campaign.id,
        'queued': sum(job.status in ('queued', 'awaiting_lookup') for job in jobs),
        'started': [],
        'skipped': skipped,
    }


def launch(session: Session, user: User, lead_ids: list[int], reason: str = '') -> dict:
    """Draft and send in one call, for callers that skip the composer.

    The reason, when given, overrides each lead's own signal in `{{reason}}`,
    which is what the Lead Pool's older one-step campaign action passed.
    """
    if not lead_ids:
        return {'started': [], 'skipped': []}
    campaign, left_behind = create_draft(
        session, user, lead_ids, allow_empty=True, group_mode=False,
    )
    if (reason or '').strip():
        campaign.outreach_reason = reason.strip()[:300]
        session.commit()

    result = send(session, user, campaign)
    # The draft could not hold every id, and the caller asked about all of them.
    result['skipped'].extend(left_behind)
    return result


# -- reporting -------------------------------------------------------------


def _counts_by_campaign(session: Session, user: User,
                        campaign_ids: set[int] | None = None) -> tuple[dict, dict, dict]:
    """Properties selected, reached, and replied for each campaign.

    Campaign reporting is property-based: two contact numbers for the same
    canonical address are one lead in the overview. Messages still remain
    separate delivery attempts, and one thread can still carry different
    properties from different campaigns.
    """
    scope = brokerage_user_ids(session, user)
    member_properties: dict[int, set[str]] = {}
    member_query = (session.query(Lead.id, Lead.campaign_id, Lead.property_address)
                    .filter(Lead.user_id.in_(scope), Lead.campaign_id.isnot(None)))
    if campaign_ids is not None:
        member_query = member_query.filter(Lead.campaign_id.in_(campaign_ids))
    member_rows = member_query.all()
    for lead_id, campaign_id, property_address in member_rows:
        property_key = address_key.canonical(property_address) or f'__lead_{lead_id}'
        member_properties.setdefault(campaign_id, set()).add(property_key)
    member_counts = {
        campaign_id: len(properties)
        for campaign_id, properties in member_properties.items()
    }

    # Only provider-accepted messages count as reached. Submission placeholders
    # and retries stay out, as do later carrier-rejected messages.
    non_delivered_statuses = (
        'failed', 'gw_timeout', 'dlr_timeout', 'delivery_failed',
        'sending_failed', 'delivery_unconfirmed', 'rejected', 'undelivered',
        'submission_queued', 'submission_retry',
    )
    delivered_properties: dict[int, set[str]] = {}
    delivered_query = (session.query(Message.id, Message.campaign_id, Message.property_key)
                       .join(Conversation, Conversation.id == Message.conversation_id)
                       .filter(Message.campaign_id.isnot(None),
                               Message.event_type == 'outreach.initial',
                               ~Message.status.in_(non_delivered_statuses),
                               Conversation.user_id.in_(scope)))
    if campaign_ids is not None:
        delivered_query = delivered_query.filter(Message.campaign_id.in_(campaign_ids))
    delivered_rows = delivered_query.all()
    for message_id, campaign_id, property_key in delivered_rows:
        key = property_key or f'__message_{message_id}'
        delivered_properties.setdefault(campaign_id, set()).add(key)
    delivered = {
        campaign_id: len(properties)
        for campaign_id, properties in delivered_properties.items()
    }

    # Attribute an inbound reply to the most recent campaign outreach in its
    # thread. Crediting every earlier campaign on a shared phone-number thread
    # would double-count one owner reply and inflate conversion analytics.
    timeline_query = (session.query(Message)
                      .join(Conversation, Conversation.id == Message.conversation_id)
                      .filter(Conversation.user_id.in_(scope)))
    if campaign_ids is not None:
        conversation_ids = [row[0] for row in (
            session.query(Message.conversation_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Conversation.user_id.in_(scope),
                    Message.campaign_id.in_(campaign_ids),
                    Message.event_type == 'outreach.initial')
            .distinct().all()
        )]
        if not conversation_ids:
            return member_counts, delivered, {}
        timeline_query = timeline_query.filter(Message.conversation_id.in_(conversation_ids))
    timeline = timeline_query.order_by(
        Message.conversation_id, Message.created_at, Message.id,
    ).all()
    latest_campaign: dict[int, tuple[int, str]] = {}
    replied_properties: set[tuple[int, str]] = set()
    for message in timeline:
        if message.event_type == 'outreach.initial' and message.campaign_id is not None:
            property_key = message.property_key or f'__message_{message.id}'
            latest_campaign[message.conversation_id] = (message.campaign_id, property_key)
        elif message.direction == 'inbound' and message.conversation_id in latest_campaign:
            replied_properties.add(latest_campaign[message.conversation_id])
    replied: dict[int, int] = {}
    for campaign_id, _property_key in replied_properties:
        replied[campaign_id] = replied.get(campaign_id, 0) + 1
    return member_counts, delivered, replied


def _stats(campaign: Campaign, members_count: int, delivered: int, replied: int) -> dict:
    return {
        'id': campaign.id,
        'name': campaign.name,
        'message_template': campaign.message_template,
        'outreach_reason': campaign.outreach_reason or '',
        'group_reason_mode': campaign.reason_groups is not None,
        'status': campaign.status,
        'sent_at': campaign.sent_at,
        'created_at': campaign.created_at,
        'recipients': members_count,
        'delivered': delivered,
        'replied': replied,
        # Every selected property that has not replied is shown as "No reply",
        # including addresses whose phone attempt was rejected by the carrier.
        'no_reply': max(members_count - replied, 0),
        # Members the send could not reach: no phone, on the DNC list, already
        # in another thread. Zero for a draft, which has not tried yet.
        'not_sent': max(members_count - delivered, 0) if campaign.status == 'sent' else 0,
    }


def overview(session: Session, user: User, progress=None) -> list[dict]:
    """Every campaign this broker owns, newest first, with its counts."""
    campaigns = (session.query(Campaign)
                 .filter(Campaign.user_id.in_(brokerage_user_ids(session, user)))
                 .order_by(Campaign.created_at.desc(), Campaign.id.desc())
                 .all())
    total = len(campaigns)
    if progress:
        progress(total, 0)
    member_counts, delivered, replied = _counts_by_campaign(session, user)
    owners = {owner.id: owner for owner in session.query(User).filter(
        User.id.in_({campaign.user_id for campaign in campaigns})).all()} if campaigns else {}
    rows = []
    for loaded, campaign in enumerate(campaigns, start=1):
        stats = _stats(campaign, member_counts.get(campaign.id, 0),
                       delivered.get(campaign.id, 0), replied.get(campaign.id, 0))
        owner = owners.get(campaign.user_id)
        stats.update(
            broker_id=owner.id if owner else campaign.user_id,
            broker_name=(owner.full_name or owner.email) if owner else 'Unknown broker',
            broker_email=owner.email if owner else None,
            broker_role=owner.role if owner else None,
        )
        rows.append(stats)
        if progress:
            progress(total, loaded)
    return rows


def suggest_reasons(session: Session, user: User, campaign: Campaign,
                    signal: str | None = None) -> dict:
    """Reason wordings that fit what this set of leads has in common."""
    return reason_suggest.suggest(session, user, campaign, signal=signal)


def detail(session: Session, user: User, campaign: Campaign,
           preview_page: int = 1, filters: dict | None = None) -> dict:
    member_counts, delivered, replied = _counts_by_campaign(
        session, user, {campaign.id},
    )
    stats = _stats(campaign, member_counts.get(campaign.id, 0),
                   delivered.get(campaign.id, 0), replied.get(campaign.id, 0))
    owner = session.query(User).filter(User.id == campaign.user_id).first()
    stats.update(
        broker_id=owner.id if owner else campaign.user_id,
        broker_name=(owner.full_name or owner.email) if owner else 'Unknown broker',
        broker_email=owner.email if owner else None,
        broker_role=owner.role if owner else None,
    )
    stats['preview'] = preview(
        session, user, campaign, page=preview_page, filters=filters,
    )
    return stats
