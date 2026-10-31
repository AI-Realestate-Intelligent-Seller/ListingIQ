"""SMS orchestration — port of the server.js message/AI pipeline.

Everything here is scoped to a single Conversation row, which belongs to exactly
one broker (`Conversation.user_id`), so two brokers texting the same number keep
separate threads.
"""

import json
import logging
import re
from datetime import datetime, timezone
from uuid import uuid4

import requests

from ..ai.client import AiUnavailableError
from .prompt import PersonaContextOverflowError
from ..core.config import settings
from ..db import SessionLocal
from ..logger import get_logger, log_event
from ..models import Conversation, Lead, Message
from . import bobbie
from .knowledge import is_agent_knowledge_question, is_experience_question
from .runtime_context import (
    AgentResolutionError,
    resolve_agent_runtime_context,
    runtime_context_is_current,
)
from .classifier import classify_lead_message, finalized_lead_status, is_opt_out, merge_lead_status, seller_signals
from .policy import (
    ai_debug, ai_debug_begin, ai_debug_enabled, ai_debug_safely, ai_debug_set, ai_debug_state, asked_questions,
    escalation_reason, estimate_sms_segments, meeting_offer_permitted, visit_only_refusal,
    EXPERIENCE_FALLBACK, find_conversation_repetition, find_reply_policy_violations,
)
from .scheduling import (
    contains_scheduling_pressure,
    contains_time_proposal,
    contains_unbooked_confirmation,
    answers_meeting_invitation_with_time,
    mentions_availability,
    preferred_availability,
)

logger = get_logger(__name__)

# Default sending number; TELNYX_FROM_NUMBER overrides it.
FIXED_FROM = '+12245798015'
TELNYX_SMS_URL = 'https://api.telnyx.com/v2/messages'


def sending_number() -> str:
    """The number every outbound SMS is sent from."""
    return settings['telnyx'].get('from_number') or FIXED_FROM

CLOSING_PATTERNS = (
    re.compile(
        r"(?:^|[,.!]\s*)(?:bye(?:\s+for\s+now)?|good\s*bye|take\s+care"
        r"|have\s+a\s+(?:(?:good|great|nice)\s+(?:day|evening|night|weekend|one)"
        r"|good\s+rest\s+of\s+(?:your|the)\s+day)"
        r"|you\s+too(?:,?\s*(?:bye|good\s*bye))?"
        r"|talk\s+(?:soon|then|tomorrow)(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?"
        r"|see\s+you\s+(?:soon|then|tomorrow(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?|at\s+[\w:]+(?:\s*[ap]m)?))"
        r"\s*[,!.]*(?:\s+[A-Za-z][A-Za-z'’-]*)?[!.]*$",
        re.I,
    ),
    re.compile(r'^(?:thanks?|thank\s+you),?\s+(?:you\s+(?:too|as\s+well)|same\s+to\s+you)\s*[!.]*$', re.I),
    re.compile(r'^(?:sounds\s+good|looking\s+forward\s+to\s+it)[,!.].*'
               r'\b(?:see\s+you|talk)\s+(?:soon|then|tomorrow|at\s+[\w:]+(?:\s*[ap]m)?)\s*[!.]*$', re.I),
)


class SmsDeliveryError(RuntimeError):
    """Outbound SMS could not be handed to the simulator or Telnyx."""

    def __init__(self, message: str, *, retryable: bool = False,
                 ambiguous: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def _phone_suffix(value: str | None) -> str:
    digits = re.sub(r'\D', '', str(value or ''))
    return digits[-4:] if digits else 'unknown'


def _sms_mode() -> str:
    mode = str(settings['telnyx'].get('mode') or 'simulation').lower()
    if mode not in ('simulation', 'telnyx'):
        raise SmsDeliveryError("SMS_MODE must be either 'simulation' or 'telnyx'")
    return mode


PHONE_PATTERN = re.compile(r'^\+[1-9]\d{6,14}$')


def _simulate_locally(body: dict) -> dict:
    """Telnyx-shaped queued response, generated in-process.

    Mirrors simulate_outbound_message() in Simulation/outbound.py so nothing
    downstream can tell the difference, without needing that service running.
    """
    to_number, text = body.get('to'), body.get('text')
    if not isinstance(to_number, str) or not PHONE_PATTERN.match(to_number):
        raise SmsDeliveryError("'to' must be a valid E.164 phone number")
    if not isinstance(text, str) or not text.strip():
        raise SmsDeliveryError("'text' must be a non-empty string")
    now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    log_event(
        logger, 'sms.delivery.simulated', destination_suffix=_phone_suffix(to_number),
        text_chars=len(text), event_type=body.get('simulation_event_type'),
    )
    return {
        'data': {
            'id': str(uuid4()),
            'record_type': 'message',
            'direction': 'outbound',
            'type': 'SMS',
            'from': {'phone_number': body.get('from')},
            'to': [{'phone_number': to_number, 'status': 'queued'}],
            'text': text,
            'received_at': now,
            'sent_at': None,
            'completed_at': None,
            'cost': {'amount': '0.0000', 'currency': 'USD'},
            'simulation': True,
        }
    }


def send_sms(payload: dict) -> dict:
    """Send an outbound message: in-process simulation, the external simulator, or Telnyx."""
    mode = _sms_mode()
    is_simulation = mode == 'simulation'
    # Always send from the configured number to prevent accidental overrides.
    body = {**payload, 'from': sending_number()}
    headers = {'Content-Type': 'application/json'}
    log_event(
        logger, 'sms.delivery.started', mode=mode,
        destination_suffix=_phone_suffix(body.get('to')),
        text_chars=len(str(body.get('text') or '')),
        event_type=body.get('simulation_event_type'),
    )

    if is_simulation:
        url = settings['telnyx'].get('simulation_url')
        if not url:
            return _simulate_locally(body)
    else:
        url = settings['telnyx'].get('api_url') or TELNYX_SMS_URL
        for key in ('simulation_event_type', 'simulation_recipient_enabled',
                    'simulation_lead_context', 'suppress_auto_reply'):
            body.pop(key, None)
        api_key = settings['telnyx'].get('api_key')
        if not api_key:
            raise SmsDeliveryError('TELNYX_API_KEY is required when SMS_MODE=telnyx')
        headers['Authorization'] = f'Bearer {api_key}'

        # Simulation never consumes provider capacity. Every real submission,
        # including manual and automated SMS outside campaigns, shares this one
        # Redis window so parallel campaigns cannot each claim 50/s.
        from .rate_limit import RateLimiterUnavailable, acquire_submission_slot
        try:
            acquire_submission_slot()
        except RateLimiterUnavailable as error:
            raise SmsDeliveryError(str(error), retryable=True) from error

    try:
        response = requests.post(
            url, json=body, headers=headers,
            timeout=settings['sms_submission']['request_timeout_seconds'],
        )
    except requests.Timeout as exc:
        # The provider may have accepted the request before the client timed
        # out. Retrying could create a duplicate SMS, so this is terminal and
        # deliberately distinct from an explicit rejection.
        raise SmsDeliveryError(str(exc), ambiguous=True) from exc
    except requests.RequestException as exc:
        if is_simulation:
            raise SmsDeliveryError(
                f'Outbound simulator is unavailable at {url}. Start it with: python3 Simulation/outbound.py'
            ) from exc
        raise SmsDeliveryError(str(exc), ambiguous=True) from exc

    try:
        parsed = response.json()
    except ValueError:
        raise SmsDeliveryError(
            f"{'Outbound simulator' if is_simulation else 'Telnyx'} responded with non-JSON: {response.text[:200]}",
            ambiguous=bool(response.ok),
        )
    if not response.ok:
        errors = parsed.get('errors') or [{}]
        detail = errors[0].get('detail') if isinstance(errors, list) and errors else json.dumps(parsed)
        retry_after = None
        try:
            retry_after = float(response.headers.get('Retry-After', ''))
        except (TypeError, ValueError):
            pass
        raise SmsDeliveryError(
            f"{'Outbound simulator' if is_simulation else 'Telnyx'} error: {detail}",
            retryable=response.status_code == 429 or 500 <= response.status_code < 600,
            retry_after=retry_after,
        )
    data = parsed.get('data') or {}
    if not data.get('id'):
        raise SmsDeliveryError(
            f"{'Outbound simulator' if is_simulation else 'Telnyx'} accepted the request without a message id.",
            ambiguous=True,
        )
    log_event(
        logger, 'sms.delivery.accepted', mode=mode,
        provider_message_id=data.get('id'), provider_status=data.get('status'),
    )
    return parsed


def send_and_store_message(
    session,
    conversation: Conversation,
    text: str,
    event_type: str = 'message.sent',
    suppress_auto_reply: bool = False,
    sender_user_id: int | None = None,
) -> Message:
    log_event(
        logger, 'sms.outbound.preparing',
        conversation_id=conversation.id,
        event_type=event_type,
        text_chars=len(text),
        estimated_segments=estimate_sms_segments(text)['segments'],
        suppress_auto_reply=suppress_auto_reply,
    )
    lead_context = None

    if conversation.lead_context:
        try:
            lead_context = json.loads(
                conversation.lead_context
            )
        except ValueError:
            lead_context = None

    response = send_sms({
        'to': conversation.contact,
        'text': text,
        'simulation_event_type': event_type,
        'suppress_auto_reply': suppress_auto_reply,
    })

    message = Message(
        conversation_id=conversation.id,
        direction='outbound',
        from_number=sending_number(),
        to_number=conversation.contact,
        text=text,
        status='queued',
        event_type=event_type,

        # NEW
        sender_user_id=sender_user_id,

        telnyx_id=(
            response.get('data') or {}
        ).get('id'),

        created_at=datetime.utcnow(),
    )

    session.add(message)
    session.commit()
    session.refresh(message)
    log_event(
    logger, 'sms.outbound.stored',
    conversation_id=conversation.id,
    message_id=message.id,
    event_type=event_type,
    status=message.status,
    provider_message_id=message.telnyx_id,
)
    return message


# ---------------------------------------------------------------------------
# Conversation state helpers
# ---------------------------------------------------------------------------

def history_for(session, conversation: Conversation) -> list:
    messages = (session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
                .all())
    log_event(logger, 'sms.history.loaded', conversation_id=conversation.id,
              message_count=len(messages))
    return [{'direction': message.direction, 'text': message.text or ''} for message in messages]


def is_conversation_closing(text: str = '') -> bool:
    normalized = re.sub(r'^[^\w]+', '', str(text or '').strip(), flags=re.UNICODE)
    if not normalized or '?' in normalized:
        return False
    return any(pattern.search(normalized) for pattern in CLOSING_PATTERNS)


def update_lead_progress(session, conversation: Conversation, **values) -> None:
    transitions = {
        key: {'from': getattr(conversation, key, None), 'to': value}
        for key, value in values.items()
        if getattr(conversation, key, None) != value
    }

    for key, value in values.items():
        setattr(conversation, key, value)
    session.add(conversation)
    session.commit()
    if transitions:
        log_event(logger, 'sms.conversation.state_updated', conversation_id=conversation.id,
                  transitions=transitions)


def _lead_id_for(session, conversation: Conversation) -> int | None:
    lead = session.query(Lead).filter(Lead.conversation_id == conversation.id).first()
    return lead.id if lead else None


def _log_status_change(session, conversation: Conversation, previous_status: str, new_status: str) -> None:
    """Records a lead_status move the AI pipeline made on its own — the
    classifier on every inbound reply, and the disposition/closing logic.
    Manual overrides from the Follow-ups status menu log their own event
    where the broker/agent making the change is known.
    """
    if previous_status == new_status:
        return
    lead_id = _lead_id_for(session, conversation)
    if not lead_id:
        return
    from ..leads import events as lead_events
    lead_events.log_event(
        session, lead_id, lead_events.STAGE, 'stage_changed',
        actor_type='ai', from_value=previous_status, to_value=new_status,
    )
    log_event(logger, 'sms.lead_status.timeline_recorded', conversation_id=conversation.id,
              lead_id=lead_id, previous_status=previous_status, new_status=new_status)


def handoff_recipient_ids(session, conversation: Conversation) -> list[int]:
    """Who is told about a handoff: the lead's assigned agent(s), plus whoever
    currently hears this owner's replies.

    The reply-notification rule alone follows recent human activity, so a
    broker who once texted the owner would be notified instead of the agent
    now assigned. Every id stays inside the conversation owner's brokerage and
    must be an active lead worker; the conversation owner is the fallback.
    """
    from ..models import User
    from ..routes.webhooks import _LEAD_WORKER_ROLES, _reply_recipient_ids
    from ..tenancy import brokerage_user_ids
    owner = session.get(User, conversation.user_id)
    tenant_ids = set(brokerage_user_ids(session, owner)) if owner else {conversation.user_id}
    assigned_ids = sorted({
        user_id for (user_id,) in session.query(Lead.assigned_agent_id).filter(
            Lead.conversation_id == conversation.id, Lead.assigned_agent_id.isnot(None)).all()})
    assigned = [user_id for (user_id,) in session.query(User.id).filter(
        User.id.in_(assigned_ids), User.id.in_(tenant_ids), User.role.in_(_LEAD_WORKER_ROLES),
        User.is_active.is_(True)).order_by(User.id).all()] if assigned_ids else []
    return list(dict.fromkeys(assigned + list(_reply_recipient_ids(session, conversation))))


def _notify_handoff(session, conversation: Conversation, title: str, message: str) -> None:
    """In-app/WebSocket/push notice to the agent(s) who must act. Never raises."""
    try:
        from ..reminder.notification import dashboard_url, notify_user
        recipients = handoff_recipient_ids(session, conversation)
        for user_id in recipients:
            notify_user(session, user_id, 'AI_HANDOFF', title, message,
                        action_url=dashboard_url({'view': 'followups', 'conversation_id': conversation.id}))
        log_event(logger, 'sms.handoff.notified', conversation_id=conversation.id, recipients=len(recipients))
    except Exception as error:  # a notification failure must not undo the handoff
        session.rollback()
        log_event(logger, 'sms.handoff.notify_failed', level=logging.WARNING,
                  conversation_id=conversation.id, error_type=type(error).__name__)


def hand_to_broker(session, conversation: Conversation, reason: str | None = None,
                   meta: dict | None = None, agent_name: str | None = None) -> None:
    """Bobbie stops; the broker owns every further reply on this thread.

    Autopilot is switched off and ownership recorded, so an inbound message
    afterwards is left pending for the broker instead of waking Bobbie.
    ``reason`` (and structured ``meta``) are shown on the lead timeline, and
    the agent is notified, so they know why and can continue.
    """
    from ..leads import events as lead_events
    was_ai = conversation.handled_by != 'broker'
    update_lead_progress(session, conversation, ai_enabled=False, handled_by='broker',
                         next_followup_at=None)
    log_event(logger, 'sms.handoff.completed', conversation_id=conversation.id,
              from_owner='bobbie' if was_ai else 'broker', to_owner='broker', ai_enabled=False)
    lead_id = _lead_id_for(session, conversation)
    if agent_name:
        # The timeline names the agent the AI spoke for, not a fixed product name.
        meta = {**(meta or {}), 'ai_agent_name': agent_name}
    if lead_id and was_ai:
        lead_events.log_event(
            session, lead_id, lead_events.OWNERSHIP, 'handover_to_agent',
            actor_type='ai', from_value='bobbie', to_value='broker', reason=reason, meta=meta,
        )
    if reason and was_ai:
        speaker = f'AI for {agent_name}' if agent_name else 'AI'
        _notify_handoff(session, conversation,
                        f'{speaker} handed {conversation.name or "a homeowner"} to you', reason)


def hand_to_bobbie(session, conversation: Conversation) -> None:
    """Broker explicitly gives the thread back to Bobbie."""
    from ..leads import events as lead_events
    was_broker = conversation.handled_by == 'broker'
    update_lead_progress(session, conversation, ai_enabled=True, handled_by='bobbie')
    from .followup_scheduler import resume_silent_followup_cadence
    resume_silent_followup_cadence(session, conversation)
    log_event(logger, 'sms.handoff.completed', conversation_id=conversation.id,
              from_owner='broker' if was_broker else 'bobbie', to_owner='bobbie', ai_enabled=True)
    lead_id = _lead_id_for(session, conversation)
    if lead_id and was_broker:
        lead_events.log_event(
            session, lead_id, lead_events.OWNERSHIP, 'handover_to_ai',
            actor_type='broker', actor_id=conversation.user_id,
            from_value='broker', to_value='bobbie',
        )


def mark_lead_completed(session, conversation: Conversation, **values) -> None:
    update_lead_progress(
        session, conversation,
        **{
            **values,
            'queue_status': 'completed',
            'lead_status': values.get('lead_status') or finalized_lead_status(conversation.lead_status),
            'processed_at': values.get('processed_at') or datetime.utcnow(),
            'next_followup_at': None,
        },
    )


def log_first_reply(session, conversation: Conversation, message: Message) -> None:
    """Records the lead's first-ever inbound reply on this thread. Later
    replies are ordinary conversation, not a timeline-worthy event."""
    lead_id = _lead_id_for(session, conversation)
    if not lead_id:
        return
    prior_inbound = session.query(Message).filter(
        Message.conversation_id == conversation.id,
        Message.direction == 'inbound',
        Message.id != message.id,
    ).count()
    if prior_inbound:
        return
    from ..leads import events as lead_events
    lead_events.log_event(session, lead_id, lead_events.ACTIVITY, 'first_reply_received')
    log_event(logger, 'sms.first_reply.timeline_recorded', conversation_id=conversation.id,
              lead_id=lead_id, message_id=message.id)


def record_inbound_classification(session, conversation: Conversation, text: str) -> dict | None:
    classification = classify_lead_message(text)
    if not classification:
        log_event(logger, 'sms.classification.completed', conversation_id=conversation.id,
                  changed=False, lead_status=conversation.lead_status, terminal=False)
        return None
    previous_status = conversation.lead_status
    lead_status = merge_lead_status(conversation.lead_status, classification.get('lead_status'))
    updates = {'lead_status': lead_status}
    if 'dnc_alert' in classification:
        updates['dnc_alert'] = bool(classification['dnc_alert'])
    update_lead_progress(session, conversation, **updates)
    _log_status_change(session, conversation, previous_status, lead_status)
    log_event(logger, 'sms.classification.completed', conversation_id=conversation.id,
              changed=previous_status != lead_status, previous_status=previous_status,
              lead_status=lead_status, terminal=bool(classification.get('terminal')),
              dnc=bool(classification.get('dnc_alert')))
    return {**classification, 'lead_status': lead_status}


# ---------------------------------------------------------------------------
# The AI reply pipeline
# ---------------------------------------------------------------------------

@ai_debug_safely
def _debug_final(reply: str, event_type: str, source: str | None = None) -> None:
    if source:
        ai_debug_set(source=source)
    ai_debug('FINAL', 'outgoing SMS', text=reply, chars=len(reply or ''),
             source=ai_debug_state().get('source'), event_type=event_type)


def _previous_agent_text(history: list) -> str:
    """The agent message immediately before the owner's latest message ('' if none)."""
    for index in range(len(history) - 1, -1, -1):
        if history[index].get('direction') == 'inbound':
            previous = history[index - 1] if index else {}
            return str(previous.get('text') or '') if previous.get('direction') == 'outbound' else ''
    return ''


def _meeting_kind(history: list, latest_inbound: str) -> str:
    """'call' when the owner declined a visit or asked for a call, else 'visit'."""
    inbound = [str(item.get('text') or '') for item in history if item.get('direction') == 'inbound']
    if any(visit_only_refusal(text) for text in inbound):
        return 'call'
    if re.search(r'\b(?:call|phone)\b', latest_inbound or '', re.I) and not re.search(
            r'\b(?:visit|stop by|come by|walk-?through|in person)\b', latest_inbound or '', re.I):
        return 'call'
    return 'visit'


def scheduling_handoff_details(conversation: Conversation, history: list, latest_inbound: str,
                               requested_time_text: str = '') -> tuple[str, str, dict]:
    """Owner-facing acknowledgment, timeline reason and structured details for a scheduling handoff.

    Only what the owner actually said is used; the price is labeled owner-stated.
    """
    inbound = [str(item.get('text') or '') for item in history if item.get('direction') == 'inbound']
    signals = [seller_signals(text) for text in inbound]
    selling = any(item['selling_intent'] for item in signals)
    urgent = any(item['urgency'] for item in signals)
    price = next((item['target_price_text'] for item in reversed(signals) if item['target_price']), None)
    kind = _meeting_kind(history, latest_inbound)
    availability = preferred_availability(requested_time_text, latest_inbound)

    noun = 'call' if kind == 'call' else 'visit'
    # Clock times are never echoed, so the reply cannot read as a confirmed slot;
    # the agent still receives the exact words below.
    spoken = availability if availability and not any(char.isdigit() for char in availability) else ''
    # Noted as a preference only: nothing here says the agent approved the time.
    reply = ((f"Thanks, I've noted {spoken} as your preference. " if spoken
              else "Thanks, I've noted your availability. ")
             + f'Our team will reach out to coordinate the {noun}.')
    parts = []
    if selling:
        parts.append('Seller interested in selling' + (' quickly' if urgent else '') + '.')
    if price:
        parts.append(f'Target price: {price} (owner-stated, unverified).')
    parts.append('Requested ' + ('phone consultation.' if kind == 'call' else 'onsite assessment.'))
    parts.append(f'Preferred availability: {availability or (requested_time_text or "").strip()[:80] or "not stated"}.')
    if conversation.property_address:
        parts.append(f'Property: {conversation.property_address}.')
    parts.append(f'Last customer message: "{" ".join(latest_inbound.split())[:160]}".')
    parts.append('Nothing is booked. Agent to coordinate and confirm the appointment.')
    meta = {
        'handoff_reason': 'seller_availability_collected',
        'conversation_id': conversation.id,
        'property_address': conversation.property_address,
        'selling_intent': selling,
        'urgency': 'high' if urgent else None,
        'owner_stated_target_price': price,
        'meeting_type': 'phone_call' if kind == 'call' else 'onsite_visit',
        'preferred_availability': availability or (requested_time_text or '').strip()[:80] or None,
        'last_customer_message': latest_inbound[:500],
        'booked': False,
    }
    return reply, ' '.join(parts), meta


# Reviewer issues that only concern how well a draft used the evidence.
_CLAIM_ONLY_ISSUES = ('unsupported_claim', 'ignores_message')


def _experience_answer_after_rejection(latest_inbound: str, history: list, issues: list, agent_name: str) -> str:
    """A fixed transparent answer when every draft to an experience question was
    rejected only for claim support or relevance; '' when a human must take over.

    Any other blocking category (fabrication, guarantee, identity, consent,
    booking, advice) still hands the thread to a human.
    """
    if not is_experience_question(latest_inbound) or not issues:
        return ''
    if not all(str(issue).strip().lower().startswith(_CLAIM_ONLY_ISSUES) for issue in issues):
        return ''
    if find_conversation_repetition(EXPERIENCE_FALLBACK, history) or find_reply_policy_violations(
            EXPERIENCE_FALLBACK, history=history, agent_name=agent_name):
        return ''
    return EXPERIENCE_FALLBACK


def process_ai_reply(conversation_id: int, latest_inbound_text: str) -> None:
    """Port of processAiReply(). Runs in a background thread with its own session."""
    session = SessionLocal()
    try:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            log_event(logger, 'sms.ai.skipped', level=logging.WARNING,
                      conversation_id=conversation_id, reason='conversation_not_found')
            return
        if not conversation.ai_enabled or conversation.handled_by != 'bobbie':
            log_event(logger, 'sms.ai.skipped', conversation_id=conversation_id,
                      reason='autopilot_inactive', ai_enabled=conversation.ai_enabled,
                      handled_by=conversation.handled_by)
            return
        log_event(logger, 'sms.ai.started', conversation_id=conversation.id,
                  user_id=conversation.user_id, inbound_chars=len(latest_inbound_text or ''),
                  lead_status=conversation.lead_status)
        if ai_debug_enabled():
            try:
                inbound = (session.query(Message.id)
                           .filter(Message.conversation_id == conversation.id, Message.direction == 'inbound')
                           .order_by(Message.id.desc()).first())
            except Exception:  # debug output only
                inbound = None
            ai_debug_begin(conversation.id, inbound[0] if inbound else None)
            ai_debug('START', 'Bobbie reply pipeline started', owner_message=latest_inbound_text)
        # Only one branch below runs per call (each returns), so the status
        # this conversation carried on entry is the "from" side of whichever
        # move happens.
        status_before = conversation.lead_status

        if is_opt_out(latest_inbound_text):
            log_event(logger, 'sms.ai.opt_out_detected', conversation_id=conversation.id)
            reply = 'Understood — I’ll remove you from my outreach list. Take care.'
            ai_debug('FALLBACK', 'HARDCODED reply selected', rule='opt-out (STOP/DNC) detected', text=reply)
            _debug_final(reply, 'ai.reply', 'fallback')
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation, lead_status='dnc', dnc_alert=True)
            _log_status_change(session, conversation, status_before, 'dnc')
            send_and_store_message(session, conversation, reply, 'ai.reply')
            log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                      outcome='opt_out_acknowledged')
            return

        # Identity is resolved only after opt-out handling: a STOP must be honored
        # even when the owning account can no longer be resolved.
        agent_context = resolve_agent_runtime_context(session, conversation)
        history = history_for(session, conversation)
        disposition = bobbie.analyze_conversation_disposition(
            conversation, history, agent_context=agent_context)
        log_event(
            logger, 'sms.ai.disposition.completed', conversation_id=conversation.id,
            action=disposition.get('action'), intent=disposition.get('intent'),
            lead_status=disposition.get('lead_status'),
            conversation_stage=disposition.get('conversation_stage'),
            next_step=disposition.get('next_step'),
            calendar_state=(disposition.get('calendar') or {}).get('state'),
            should_fetch_availability=(disposition.get('calendar') or {}).get(
                'should_fetch_availability'),
        )
        ai_debug('DISPOSITION', 'model disposition', action=disposition.get('action'),
                 intent=disposition.get('intent'), next_step=disposition.get('next_step'),
                 calendar_state=(disposition.get('calendar') or {}).get('state'),
                 should_fetch_availability=(disposition.get('calendar') or {}).get('should_fetch_availability'),
                 stage=disposition.get('conversation_stage'), lead_status=disposition.get('lead_status'),
                 reason=disposition.get('reason'))

        if disposition['action'] == 'end':
            closing_context = 'Do not schedule; this conversation is ending.'
            closing_control = (
                f"Final disposition: END. Intent: {disposition['intent']}. Reason: {disposition['reason']}. "
                'Write one brief, respectful closing acknowledgment suited to the owner’s latest message. '
                'Do not ask any question, offer another service, request a call, mention scheduling, or try to continue.'
            )
            closing_evidence = []
            # One knowledge retrieval per processing attempt, reused by every regeneration.
            closing_snapshot: dict = {}
            ai_debug_set(phase='closing')
            closing_reply = bobbie.generate_ai_reply(conversation, history, closing_context, closing_control,
                                                     evidence_out=closing_evidence,
                                                     agent_context=agent_context,
                                                     knowledge_snapshot=closing_snapshot)
            try:
                closing_reply = bobbie.review_and_repair_ai_reply(
                    conversation, history, closing_reply, disposition, closing_context, closing_control,
                    knowledge_evidence=closing_evidence, agent_context=agent_context,
                    knowledge_snapshot=closing_snapshot)
            except bobbie.ReviewRejectedError:
                # Every generated closing was rejected: the fixed closing below
                # is pre-written text, not model output.
                closing_reply = ''
            if not closing_reply or '?' in closing_reply or contains_scheduling_pressure(closing_reply):
                log_event(logger, 'sms.ai.closing.fallback', level=logging.WARNING,
                          conversation_id=conversation.id, reason='unsafe_or_empty_draft')
                closing_reply = 'Understood. Thanks for letting me know, and take care.'
                ai_debug('FALLBACK', 'HARDCODED closing reply selected',
                         rule='closing draft empty, asked a question, or pressured scheduling', text=closing_reply)
                ai_debug_set(source='fallback')
            if not runtime_context_is_current(session, conversation, agent_context):
                log_event(logger, 'sms.ai.skipped', level=logging.WARNING,
                          conversation_id=conversation.id, reason='agent_context_changed')
                return
            _debug_final(closing_reply, 'ai.closing')
            send_and_store_message(session, conversation, closing_reply, 'ai.closing')
            hand_to_broker(session, conversation, agent_name=agent_context.agent_name)
            mark_lead_completed(session, conversation, lead_status=disposition['lead_status'])
            _log_status_change(session, conversation, status_before, disposition['lead_status'])
            log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                      outcome='conversation_closed', intent=disposition['intent'])
            return

        continued_status = (disposition['lead_status']
                            if conversation.lead_status in ('not_interested', 'no_response')
                            else merge_lead_status(conversation.lead_status, disposition['lead_status']))
        update_lead_progress(session, conversation, lead_status=continued_status, processed_at=None)
        _log_status_change(session, conversation, status_before, continued_status)

        disposition_control = (
            f"Final disposition: CONTINUE. Intent: {disposition['intent']}. "
            f"Conversation stage: {disposition['conversation_stage']}. "
            f"Required next step: {disposition['next_step']}. "
            f"Qualification focus: {disposition['qualification_focus'] or 'none supplied'}. "
            f"Calendar state: {disposition['calendar']['state']}. Reason: {disposition['reason']}. "
            'Follow the required next step naturally without assuming facts. If the next step is '
            'answer_and_qualify, answer the concern and ask one concise logical question. '
            'If it is answer_only, do not ask a question. If it is offer_call, do not qualify further: acknowledge '
            'what is already known (including any price the owner hopes for and any urgency) and suggest a short '
            'onsite visit to see the property and review comparable sales before recommending a listing price, '
            'then ask when would be convenient for them (offer a phone call instead only if the owner declined a '
            'visit or asked for a call). '
            'Never propose, offer or confirm a specific day or time yourself; once the owner says when suits them, '
            'the team arranges the exact time.'
        )
        meeting_ok = meeting_offer_permitted(disposition['next_step'], latest_inbound_text,
                                             disposition['calendar']['state'])
        disposition_control += (
            ' Meeting offer permitted in this reply: yes. Prefer a short onsite visit; a quick call is also fine.'
            if meeting_ok else
            ' Meeting offer permitted in this reply: no. Do not mention, offer or ask about a visit, stop-by, '
            'call or meeting.'
        )
        if is_agent_knowledge_question(latest_inbound_text):
            # A question or challenge about the agent's approach is answered, not
            # turned into a scheduling push.
            disposition_control += (
                ' The owner asked a direct question about you or your approach. Answer it first and in substance '
                'with what you would actually do, using only supported facts and standard process (seeing the '
                'property, reviewing comparable sales, recommending a price the market supports). A meeting '
                'invitation is optional here, at most one short low-pressure line, and never replaces the answer.'
            )
        if visit_only_refusal(latest_inbound_text):
            disposition_control += (
                ' The owner declined an in-person visit. Respect that: do not suggest a visit or stop-by; offer a '
                'quick phone call or a preliminary market discussion instead.'
            )
        escalation = escalation_reason(latest_inbound_text)
        if escalation:
            disposition_control += (
                ' The owner asked for negotiation or professional advice. Do not negotiate, propose terms, or give '
                f'legal, tax or financial advice by text. Say that {agent_context.agent_name} handles that directly '
                'rather than by automated text and that someone from the team will text them shortly. Ask no question.'
            )
        already_asked = asked_questions(history)
        if already_asked:
            # The disposition can point back at a question Bobbie already asked; never re-ask it.
            disposition_control += (
                ' Questions already asked in this conversation (never ask these again, even reworded, even if '
                'unanswered; if the qualification focus is one of them, ask a different useful question or none): '
                + ' | '.join(already_asked[-6:])
            )
        calendar_state = disposition['calendar']
        log_event(logger, 'sms.calendar.state_resolved', conversation_id=conversation.id,
                  source=settings['ai']['provider'], state=calendar_state['state'],
                  should_fetch_availability=calendar_state['should_fetch_availability'])

        scheduling_context = ''
        if (not calendar_state['should_fetch_availability'] and calendar_state['state'] != 'call_declined'
                and answers_meeting_invitation_with_time(_previous_agent_text(history), latest_inbound_text)):
            # Backstop for a disposition that missed a plain availability reply to the
            # agent's own visit/call invitation: still hand off, never book.
            log_event(logger, 'sms.calendar.availability_backstop', conversation_id=conversation.id,
                      model_state=calendar_state['state'])
            calendar_state = {**calendar_state, 'state': 'time_proposed', 'should_fetch_availability': True}
        requested_time = (calendar_state.get('requested_time_text') or '').strip()
        availability_given = (calendar_state['state'] in ('time_proposed', 'booking_confirmed')
                              or bool(requested_time) or mentions_availability(latest_inbound_text))
        if calendar_state['should_fetch_availability'] and not availability_given:
            # The owner agreed to meet but has not said when: ask once, keep the thread.
            scheduling_context = ('The owner agreed to meet but has not said when. Acknowledge it and ask one natural '
                                  'question about what day or time generally suits them. Do not propose, offer or '
                                  'confirm a time, and do not claim anything is booked.')
        elif calendar_state['should_fetch_availability']:
            # The AI never books. With the owner's preferred availability in hand it
            # acknowledges it, hands the thread to the agent and stops; the agent
            # confirms the exact time against their calendar from Follow-ups.
            reply, handoff_reason, handoff_meta = scheduling_handoff_details(
                conversation, history, latest_inbound_text, requested_time)
            ai_debug('FALLBACK', 'HARDCODED reply selected', rule='scheduling handoff (availability collected)',
                     text=reply)
            _debug_final(reply, 'ai.reply', 'fallback')
            outbound = send_and_store_message(session, conversation, reply, 'ai.reply')
            previous_status = conversation.lead_status
            hand_to_broker(session, conversation, agent_name=agent_context.agent_name,
                           reason=handoff_reason, meta=handoff_meta)
            update_lead_progress(session, conversation, lead_status='location_discussion',
                                 meeting_booked=False, processed_at=None)
            _log_status_change(session, conversation, previous_status, 'location_discussion')
            log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                      outcome='meeting_handed_to_human', calendar_state=calendar_state['state'],
                      message_id=outbound.id)
            return
        if calendar_state['state'] == 'call_declined':
            scheduling_context = ('The recipient declined or deferred a meeting. Answer their latest question directly. '
                                  'Do not offer times, ask for a visit or a call, or pressure them to schedule.')

        fallback_plan = {'next_step': disposition['next_step'],
                         'calendar_state': disposition['calendar']['state']}
        # Knowledge results the reviewed draft was written from.
        reply_evidence = []
        # One knowledge retrieval per processing attempt: retries and reviewer
        # regenerations reuse this exact evidence snapshot.
        knowledge_snapshot: dict = {}
        reply = bobbie.generate_ai_reply(
            conversation, history, scheduling_context, disposition_control, fallback_plan,
            evidence_out=reply_evidence, agent_context=agent_context, knowledge_snapshot=knowledge_snapshot)

        if contains_unbooked_confirmation(reply) or contains_time_proposal(reply):
            log_event(logger, 'sms.ai.safety_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='unbooked_confirmation')
            ai_debug('FALLBACK', 'HARDCODED reply selected before review',
                     rule=('unbooked booking claim' if contains_unbooked_confirmation(reply)
                           else 'day/time proposal'), replaced=reply)
            reply = 'I haven’t scheduled anything, and I won’t invent a time.'
            ai_debug_set(source='fallback')

        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            log_event(logger, 'sms.ai.safety_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='pressure_after_refusal')
            retry_context = (f'{scheduling_context}\nThe previous draft improperly reintroduced a call. '
                             'Reply directly by text without mentioning a call, meeting, schedule, appointment, '
                             'email, or future follow-up.')
            retry_evidence = []
            ai_debug('FALLBACK', 'draft pressured scheduling after the owner declined; regenerating once',
                     rule='call_declined + scheduling pressure', replaced=reply)
            ai_debug_set(phase='call_declined_retry')
            retry = bobbie.generate_ai_reply(conversation, history, retry_context, disposition_control, fallback_plan,
                                             evidence_out=retry_evidence, agent_context=agent_context,
                                             knowledge_snapshot=knowledge_snapshot)
            reply = ('Understood—I’ll keep this to text and answer what I can here.'
                     if contains_scheduling_pressure(retry) else retry)
            ai_debug_set(phase='original')
            if contains_scheduling_pressure(retry):
                ai_debug('FALLBACK', 'HARDCODED reply selected', rule='retry still pressured scheduling',
                         text=reply)
                ai_debug_set(source='fallback')
            reply_evidence = retry_evidence

        rejected = None
        try:
            reply = bobbie.review_and_repair_ai_reply(conversation, history, reply, disposition,
                                                      scheduling_context, disposition_control,
                                                      knowledge_evidence=reply_evidence,
                                                      agent_context=agent_context,
                                                      knowledge_snapshot=knowledge_snapshot)
        except bobbie.ReviewRejectedError as error:
            rejected = error
            reply = _experience_answer_after_rejection(latest_inbound_text, history, error.issues,
                                                       agent_context.agent_name)
        if rejected is not None and reply:
            # An experience question is not a reason to hand off: the drafts only
            # overstated evidence, so a fixed, claim-free answer is sent instead.
            log_event(logger, 'sms.ai.review_exhausted', level=logging.WARNING,
                      conversation_id=conversation.id, outcome='transparent_fallback',
                      reason='experience_question_claims_only', issues=','.join(rejected.issues))
            ai_debug('FALLBACK', 'every candidate rejected for claim support -> transparent experience answer',
                     text=reply)
            ai_debug_set(source='fallback')
        elif rejected is not None:
            # Bounded regeneration is exhausted. Send nothing generated; a human
            # owns the thread from here (no automatic retry loop).
            # Reached only for hard-block issues: soft reviewer feedback no longer rejects.
            log_event(logger, 'sms.ai.review_exhausted', level=logging.WARNING,
                      conversation_id=conversation.id, outcome='handed_to_human',
                      reason='hard_block_after_regeneration')
            ai_debug('FALLBACK', 'every candidate rejected by the reviewer -> human handoff, nothing sent')
            if runtime_context_is_current(session, conversation, agent_context):
                hand_to_broker(session, conversation, agent_name=agent_context.agent_name, reason=(
                    'AI reply withheld: the reviewer found a policy issue it could not fix ('
                    + (', '.join(rejected.issues) or 'unspecified') + '). No message was sent; please reply to the owner.'
                ))
            return

        if contains_unbooked_confirmation(reply) or contains_time_proposal(reply):
            log_event(logger, 'sms.ai.review_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='calendar_safety')
            ai_debug('FALLBACK', 'HARDCODED reply selected after review',
                     rule=('unbooked booking claim' if contains_unbooked_confirmation(reply)
                           else 'day/time proposal'), replaced=reply)
            reply = 'I haven’t scheduled anything, and I won’t invent a time.'
            ai_debug_set(source='fallback')
        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            log_event(logger, 'sms.ai.review_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='pressure_after_refusal')
            ai_debug('FALLBACK', 'HARDCODED reply selected after review',
                     rule='call_declined + scheduling pressure', replaced=reply)
            reply = 'Understood—I’ll keep this to text and answer what I can here.'
            ai_debug_set(source='fallback')

        if not runtime_context_is_current(session, conversation, agent_context):
            log_event(logger, 'sms.ai.skipped', level=logging.WARNING,
                      conversation_id=conversation.id, reason='agent_context_changed')
            return
        _debug_final(reply, 'ai.reply')
        outbound = send_and_store_message(session, conversation, reply, 'ai.reply')
        if escalation:
            # Genuine escalation: the reply above was sent, then a human owns the thread.
            hand_to_broker(session, conversation, agent_name=agent_context.agent_name, reason=f'Owner asked for {escalation.replace("_", " ")}; '
                           'the AI replied and handed the thread to the agent.')
            log_event(logger, 'sms.ai.escalated', conversation_id=conversation.id, reason=escalation)
        elif is_conversation_closing(reply):
            hand_to_broker(session, conversation, agent_name=agent_context.agent_name)
            mark_lead_completed(session, conversation)
            log_event(logger, 'sms.ai.autopilot_stopped', conversation_id=conversation.id,
                      reason='closing_reply')
        segments = estimate_sms_segments(reply)
        log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                  outcome='reply_sent', message_id=outbound.id,
                  text_chars=len(reply), sms_encoding=segments['encoding'],
                  estimated_segments=segments['segments'],
                  estimated_cost_usd=segments.get('estimated_cost_usd'))
    except PersonaContextOverflowError as error:
        # A retry cannot succeed until the Persona is shortened, so the agent
        # takes over now and is told why. Nothing was sent to the owner.
        log_event(logger, 'sms.ai.failed', level=logging.ERROR,
                  conversation_id=conversation_id, stage='persona_context',
                  error_type=type(error).__name__, error=str(error))
        try:
            session.rollback()
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None and conversation.handled_by != 'broker':
                hand_to_broker(session, conversation, reason=(
                    'AI paused: your published Persona (approved SMS examples, style and playbook) is too large '
                    f'for one AI request (about {error.estimated_tokens:,} tokens; limit {error.budget:,}). '
                    'No message was sent; please reply to the owner. Shorten or remove some conversation '
                    'examples and publish again to turn the AI back on.'),
                    meta={'handoff_reason': 'persona_context_overflow', 'conversation_id': conversation_id,
                          'estimated_tokens': error.estimated_tokens, 'budget': error.budget})
        except Exception:
            logger.exception('event=sms.ai.persona_overflow_handoff_failed conversation_id=%s', conversation_id)
    except (AiUnavailableError, AgentResolutionError) as error:
        # Nothing is sent when the model is unreachable; autopilot stays on so the
        # next inbound message retries once the configured AI provider is available.
        log_event(logger, 'sms.ai.failed', level=logging.ERROR,
                  conversation_id=conversation_id, stage='model',
                  error_type=type(error).__name__, error=str(error))
    except SmsDeliveryError as error:
        log_event(logger, 'sms.ai.failed', level=logging.ERROR,
                  conversation_id=conversation_id, stage='delivery',
                  error_type=type(error).__name__, error=str(error))
    except Exception as error:
        logger.exception(
            'event=sms.ai.failed conversation_id=%s stage=unexpected error_type=%s error=%s',
            conversation_id, type(error).__name__, error)
    finally:
        session.close()
