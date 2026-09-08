"""Campaigns — turning selected leads into Bobbie conversations.

A campaign is a named batch with one message template. The broker selects leads
in the pool, which creates a *draft* holding them, names it and writes the
opening message in the Campaigns tab, then sends. Sending renders the template
once per lead, opens a conversation and hands it to Bobbie, who answers the
replies until the broker takes the thread over from the SMS tab.

Anything that cannot be sent is reported back per lead rather than failing the
whole batch — a broker selecting forty rows should not lose thirty-nine of them
to one bad phone number.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import Campaign, Conversation, Lead, Message, User
from ..sms import service as sms_service
from ..sms.outreach import build_single_lead_context
from ..sms.followup_scheduler import schedule_initial_followup
from ..tenancy import brokerage_user_ids
from . import address as address_key
from . import events as lead_events
from . import reason_suggest
from . import template as message_template
from .importer import normalize_phone
from .service import derive_stage, lead_details, outreach_reason, serialize, split_signals

MAX_CAMPAIGN_SIZE = 200


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
    wanted = {
        (address_key.canonical(lead.property_address), normalize_phone(lead.phone or ''))
        for lead in leads
    }
    wanted = {(property_key, phone) for property_key, phone in wanted if property_key and phone}
    if not wanted:
        return NO_CLAIMS

    property_keys = sorted({property_key for property_key, _ in wanted})
    rows = (session.query(Message.property_key, Conversation.contact,
                          Conversation.user_id.in_(scope))
            .join(Conversation, Conversation.id == Message.conversation_id)
            .filter(Message.property_key.in_(property_keys),
                    Message.event_type == 'outreach.initial')
            .distinct()
            .all())
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
                 allow_empty: bool = False) -> tuple[Campaign, list[dict]]:
    """Hold a selection of leads under a new draft. Nothing is sent yet.

    Returns the draft and the leads it could not take, so the pool can say why
    a selection of forty arrived in the composer as thirty-seven.

    `allow_empty` keeps a draft that took nothing. The composer never wants one
    — there would be nothing to write to — so it defaults to raising. The
    one-shot `launch` below passes it, because its callers expect every lead
    accounted for in `skipped` rather than one error for the whole batch.
    """
    if not lead_ids:
        raise ValueError('Select at least one lead to build a campaign.')
    if len(lead_ids) > MAX_CAMPAIGN_SIZE:
        raise ValueError(f'A campaign can hold at most {MAX_CAMPAIGN_SIZE} leads.')

    campaign = Campaign(
        user_id=user.id,
        name=(name or '').strip() or _default_name(session, user),
        message_template=(template or '').strip() or message_template.DEFAULT_TEMPLATE,
        status='draft',
        created_at=datetime.utcnow(),
    )
    session.add(campaign)
    session.commit()
    session.refresh(campaign)

    left_behind = attach(session, user, campaign, lead_ids)
    if len(left_behind) == len(lead_ids) and not allow_empty:
        # Nothing could be taken, so there is no draft worth composing. Roll the
        # empty shell back rather than dropping the broker into a blank screen.
        session.delete(campaign)
        session.commit()
        reasons = sorted({row['reason'] for row in left_behind})
        raise ValueError('None of the selected leads can start a campaign. '
                         + ' '.join(reasons))
    return campaign, left_behind


def attach(session: Session, user: User, campaign: Campaign, lead_ids: list[int]) -> list[dict]:
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

    scope = brokerage_user_ids(session, user)
    found = {lead.id: lead for lead in session.query(Lead)
             .filter(Lead.user_id.in_(scope), Lead.id.in_(lead_ids))
             .all()}
    # Naming the campaign that holds a lead is more useful than "already taken".
    held_by = {row[0]: row[1] for row in session.query(Campaign.id, Campaign.name)
               .filter(Campaign.user_id.in_(scope)).all()}

    claims = claims_on(session, scope, list(found.values()))

    left_behind: list[dict] = []
    eligible: list[int] = []
    for lead_id in lead_ids:
        lead = found.get(lead_id)
        if lead is None:
            left_behind.append({'lead_id': lead_id, 'owner_name': None, 'reason': 'Lead not found.'})
        elif selection_reason(lead):
            # Not ready: it stays in the pool, where the broker can see the
            # stage and act on it. A lead held by a campaign is hidden from the
            # pool, so one that can never be sent would be hidden with no way
            # to fix it.
            left_behind.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': selection_reason(lead)})
        elif claims.reason_for(lead):
            left_behind.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': claims.reason_for(lead)})
        elif lead.campaign_id is not None and lead.campaign_id != campaign.id:
            name = held_by.get(lead.campaign_id, 'another campaign')
            left_behind.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': f'Already in the draft “{name}”.'})
        else:
            eligible.append(lead.id)

    if eligible:
        # One guarded statement rather than a field assignment per lead: the
        # WHERE clause re-checks the invariant at write time, so two drafts
        # built from overlapping selections at the same moment cannot both
        # claim the same lead.
        session.query(Lead).filter(
            Lead.user_id.in_(scope),
            Lead.id.in_(eligible),
            Lead.campaign_id.is_(None),
            Lead.conversation_id.is_(None),
        ).update({Lead.campaign_id: campaign.id}, synchronize_session=False)
        session.commit()
        session.expire_all()

        # Anything the guard rejected lost a race with another draft.
        claimed = {row[0] for row in session.query(Lead.id)
                   .filter(Lead.id.in_(eligible), Lead.campaign_id == campaign.id).all()}
        for lead_id in eligible:
            if lead_id not in claimed:
                lead = found[lead_id]
                left_behind.append({'lead_id': lead_id, 'owner_name': lead.owner_name,
                                    'reason': 'Just claimed by another campaign.'})
        for lead_id in claimed:
            # The lead is locked to this campaign from here — it can't join
            # another draft until this one is discarded (see docstring above).
            lead_events.log_event(
                session, lead_id, lead_events.STAGE, 'locked_to_campaign',
                actor_type='broker', actor_id=user.id,
                to_value=campaign.name, meta={'campaign_id': campaign.id},
            )
    else:
        session.commit()
    return left_behind


def update_draft(session: Session, user: User, campaign: Campaign,
                 name: str | None = None, template: str | None = None,
                 reason: str | None = None) -> Campaign:
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
    session.commit()
    session.refresh(campaign)
    return campaign


def members(session: Session, user: User, campaign: Campaign) -> list[Lead]:
    return (session.query(Lead)
            .filter(Lead.user_id.in_(brokerage_user_ids(session, user)),
                    Lead.campaign_id == campaign.id)
            .order_by(Lead.score.desc(), Lead.id)
            .all())


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


def _sent_view(session: Session, user: User, campaign: Campaign) -> tuple[list[dict], list[dict]]:
    """What a sent campaign actually sent, read back from the messages.

    Re-rendering the template would be a guess: the template may have been
    edited, and the lead's own reason may since have changed. The stored message
    is what the owner received, so that is what the campaign's record shows.
    """
    people = members(session, user, campaign)
    conversation_ids = [lead.conversation_id for lead in people if lead.conversation_id]

    outreach = (session.query(Message)
                .filter(Message.campaign_id == campaign.id,
                        Message.event_type == 'outreach.initial')
                .all())
    # Keyed by property as well as thread: one campaign can text the same owner
    # about two of their listings, which is two messages on one thread.
    by_property = {(row.conversation_id, row.property_key): row for row in outreach}

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
            # The reason it was held back was not recorded, so only the reasons
            # still visible on the lead can be given.
            standing = _blocking_reason_for_record(lead)
            missed_rows.append({'lead_id': lead.id, 'owner_name': lead.owner_name,
                                'reason': standing or 'Was not sent.'})
            continue
        answered = latest_reply.get(lead.conversation_id)
        sent_rows.append(_row(
            lead, message.text or '',
            conversation_id=lead.conversation_id,
            replied=bool(answered and answered >= message.created_at),
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


def preview(session: Session, user: User, campaign: Campaign) -> dict:
    """The campaign's leads: what each would read, or what each actually read.

    Every recipient is rendered rather than only the first: the whole point of a
    template is that the address and the reason change, and a broker should be
    able to scroll the real messages before two hundred of them go out.
    """
    if campaign.status == 'sent':
        sent_rows, missed_rows = _sent_view(session, user, campaign)
        return {
            'campaign_id': campaign.id,
            'recipients': sent_rows,
            'skipped': missed_rows,
            'template_error': '',
            'tokens': message_template.TOKEN_HELP,
        }

    rendered: list[dict] = []
    skipped: list[dict] = []
    people = members(session, user, campaign)
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
        text = '' if error else message_template.render(
            template, lead, user, campaign.outreach_reason or '')
        rendered.append(_row(lead, text))

    return {
        'campaign_id': campaign.id,
        'recipients': rendered,
        'skipped': skipped,
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
    key = address_key.canonical(lead.property_address)
    conversation = (session.query(Conversation)
                    .filter(Conversation.contact == lead.phone,
                            Conversation.user_id.in_(brokerage_user_ids(session, user)))
                    .first())
    already_spoken = conversation is not None and session.query(Message).filter(
        Message.conversation_id == conversation.id).count() > 0

    # Ground Bobbie in the same reason the owner was just texted, not a
    # different one derived from the lead's signals.
    why = outreach_reason(lead, campaign.outreach_reason or '')
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
    context['property_details'] = lead_details(lead)
    if previous_address and previous_address != lead.property_address:
        # An owner with more than one listing: both are approved facts, and
        # Bobbie needs the earlier one to make sense of "the other one".
        earlier = _previous_properties(conversation)
        earlier.append(previous_address)
        context['other_properties'] = earlier
    conversation.lead_context = json.dumps(context)
    # Bobbie drives the replies from here; the broker can take any thread over
    # from the SMS tab, which is what handled_by tracks.
    conversation.ai_enabled = True
    conversation.recipient_ai_enabled = False
    conversation.handled_by = 'bobbie'
    conversation.lead_status = 'processing'
    conversation.queue_status = 'idle'
    conversation.processed_at = None
    session.add(conversation)
    session.commit()
    session.refresh(conversation)

    try:
        message = sms_service.send_and_store_message(
            session, conversation, text, 'outreach.initial')
    except sms_service.SmsDeliveryError as error:
        return None, {'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': str(error)}
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
    }, None


def send(session: Session, user: User, campaign: Campaign) -> dict:
    """Render and send the draft to every eligible member. Per-lead outcomes."""
    if campaign.status != 'draft':
        raise ValueError('This campaign has already been sent.')
    message_template.validate(campaign.message_template)

    started: list[dict] = []
    skipped: list[dict] = []

    scope = brokerage_user_ids(session, user)
    for lead in members(session, user, campaign):
        # Checked per lead rather than once for the batch: a long send gives
        # another brokerage time to claim a number partway through it.
        blocked = _blocking_reason(lead, claims_on(session, scope, [lead]))
        if blocked:
            skipped.append({'lead_id': lead.id, 'owner_name': lead.owner_name, 'reason': blocked})
            continue
        text = message_template.render(campaign.message_template, lead, user,
                                       campaign.outreach_reason or '')
        sent, failed = _send_to_lead(session, user, campaign, lead, text)
        if sent:
            started.append(sent)
        elif failed:
            skipped.append(failed)

    # A campaign holds the leads it texted. Anything it could not reach returns
    # to the pool rather than sitting under a campaign that never contacted it,
    # invisible in both places.
    texted = {row['lead_id'] for row in started}
    for lead in members(session, user, campaign):
        if lead.id not in texted:
            lead.campaign_id = None

    campaign.status = 'sent'
    campaign.sent_at = datetime.utcnow()
    session.commit()
    return {'campaign_id': campaign.id, 'started': started, 'skipped': skipped}


def launch(session: Session, user: User, lead_ids: list[int], reason: str = '') -> dict:
    """Draft and send in one call, for callers that skip the composer.

    The reason, when given, overrides each lead's own signal in `{{reason}}`,
    which is what the Lead Pool's older one-step campaign action passed.
    """
    if not lead_ids:
        return {'started': [], 'skipped': []}
    campaign, left_behind = create_draft(session, user, lead_ids, allow_empty=True)
    if (reason or '').strip():
        campaign.outreach_reason = reason.strip()[:300]
        session.commit()

    result = send(session, user, campaign)
    # The draft could not hold every id, and the caller asked about all of them.
    result['skipped'].extend(left_behind)
    return result


# -- reporting -------------------------------------------------------------


def _counts_by_campaign(session: Session, user: User) -> tuple[dict, dict, dict]:
    """Members, outreach delivered, and outreach that got a reply.

    Delivery is counted per outreach message rather than per thread. One thread
    can carry outreach from two campaigns — a landlord with two listings — and
    counting threads would credit one campaign and lose the other.
    """
    scope = brokerage_user_ids(session, user)
    member_counts = dict(session.query(Lead.campaign_id, func.count(Lead.id))
                         .filter(Lead.user_id.in_(scope), Lead.campaign_id.isnot(None))
                         .group_by(Lead.campaign_id)
                         .all())

    # "Delivered" is every opening message the campaign got out without an
    # error. Carrier delivery receipts are not counted here.
    delivered = dict(session.query(Message.campaign_id, func.count(Message.id))
                     .join(Conversation, Conversation.id == Message.conversation_id)
                     .filter(Message.campaign_id.isnot(None),
                             Message.event_type == 'outreach.initial',
                             Conversation.user_id.in_(scope))
                     .group_by(Message.campaign_id)
                     .all())

    # Attribute an inbound reply to the most recent campaign outreach in its
    # thread. Crediting every earlier campaign on a shared phone-number thread
    # would double-count one owner reply and inflate conversion analytics.
    timeline = (session.query(Message)
                .join(Conversation, Conversation.id == Message.conversation_id)
                .filter(Conversation.user_id.in_(scope))
                .order_by(Message.conversation_id, Message.created_at, Message.id)
                .all())
    latest_campaign: dict[int, int] = {}
    replied_pairs: set[tuple[int, int]] = set()
    for message in timeline:
        if message.event_type == 'outreach.initial' and message.campaign_id is not None:
            latest_campaign[message.conversation_id] = message.campaign_id
        elif message.direction == 'inbound' and message.conversation_id in latest_campaign:
            replied_pairs.add((latest_campaign[message.conversation_id], message.conversation_id))
    replied: dict[int, int] = {}
    for campaign_id, _conversation_id in replied_pairs:
        replied[campaign_id] = replied.get(campaign_id, 0) + 1
    return member_counts, delivered, replied


def _stats(campaign: Campaign, members_count: int, delivered: int, replied: int) -> dict:
    return {
        'id': campaign.id,
        'name': campaign.name,
        'message_template': campaign.message_template,
        'outreach_reason': campaign.outreach_reason or '',
        'status': campaign.status,
        'sent_at': campaign.sent_at,
        'created_at': campaign.created_at,
        'recipients': members_count,
        'delivered': delivered,
        'replied': replied,
        # Only a thread that actually received the message can be "waiting".
        'no_reply': max(delivered - replied, 0),
        # Members the send could not reach: no phone, on the DNC list, already
        # in another thread. Zero for a draft, which has not tried yet.
        'not_sent': max(members_count - delivered, 0) if campaign.status == 'sent' else 0,
    }


def overview(session: Session, user: User) -> list[dict]:
    """Every campaign this broker owns, newest first, with its counts."""
    member_counts, delivered, replied = _counts_by_campaign(session, user)
    campaigns = (session.query(Campaign)
                 .filter(Campaign.user_id.in_(brokerage_user_ids(session, user)))
                 .order_by(Campaign.created_at.desc(), Campaign.id.desc())
                 .all())
    owners = {owner.id: owner for owner in session.query(User).filter(
        User.id.in_({campaign.user_id for campaign in campaigns})).all()} if campaigns else {}
    rows = []
    for campaign in campaigns:
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
    return rows


def suggest_reasons(session: Session, user: User, campaign: Campaign) -> dict:
    """Reason wordings that fit what this set of leads has in common."""
    return reason_suggest.suggest(session, user, campaign)


def detail(session: Session, user: User, campaign: Campaign) -> dict:
    member_counts, delivered, replied = _counts_by_campaign(session, user)
    stats = _stats(campaign, member_counts.get(campaign.id, 0),
                   delivered.get(campaign.id, 0), replied.get(campaign.id, 0))
    owner = session.query(User).filter(User.id == campaign.user_id).first()
    stats.update(
        broker_id=owner.id if owner else campaign.user_id,
        broker_name=(owner.full_name or owner.email) if owner else 'Unknown broker',
        broker_email=owner.email if owner else None,
        broker_role=owner.role if owner else None,
    )
    stats['preview'] = preview(session, user, campaign)
    return stats
