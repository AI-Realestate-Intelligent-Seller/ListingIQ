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

from ..core.config import settings
from ..db import SessionLocal
from ..logger import get_logger, log_event
from ..models import Conversation, Lead, Message
from . import bobbie
from .deepseek import AiUnavailableError
from .classifier import classify_lead_message, finalized_lead_status, is_opt_out, merge_lead_status
from .scheduling import (
    contains_scheduling_pressure,
    contains_time_proposal,
    contains_unbooked_confirmation,
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

    try:
        response = requests.post(url, json=body, headers=headers, timeout=15)
    except requests.RequestException as exc:
        if is_simulation:
            raise SmsDeliveryError(
                f'Outbound simulator is unavailable at {url}. Start it with: python3 Simulation/outbound.py'
            ) from exc
        raise SmsDeliveryError(str(exc)) from exc

    try:
        parsed = response.json()
    except ValueError:
        raise SmsDeliveryError(
            f"{'Outbound simulator' if is_simulation else 'Telnyx'} responded with non-JSON: {response.text[:200]}"
        )
    if not response.ok:
        errors = parsed.get('errors') or [{}]
        detail = errors[0].get('detail') if isinstance(errors, list) and errors else json.dumps(parsed)
        raise SmsDeliveryError(f"{'Outbound simulator' if is_simulation else 'Telnyx'} error: {detail}")
    data = parsed.get('data') or {}
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


def hand_to_broker(session, conversation: Conversation) -> None:
    """Bobbie stops; the broker owns every further reply on this thread.

    Autopilot is switched off and ownership recorded, so an inbound message
    afterwards is left pending for the broker instead of waking Bobbie.
    """
    from ..leads import events as lead_events
    was_ai = conversation.handled_by != 'broker'
    update_lead_progress(session, conversation, ai_enabled=False, handled_by='broker',
                         next_followup_at=None)
    log_event(logger, 'sms.handoff.completed', conversation_id=conversation.id,
              from_owner='bobbie' if was_ai else 'broker', to_owner='broker', ai_enabled=False)
    lead_id = _lead_id_for(session, conversation)
    if lead_id and was_ai:
        lead_events.log_event(
            session, lead_id, lead_events.OWNERSHIP, 'handover_to_agent',
            actor_type='ai', from_value='bobbie', to_value='broker',
        )


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
        # Only one branch below runs per call (each returns), so the status
        # this conversation carried on entry is the "from" side of whichever
        # move happens.
        status_before = conversation.lead_status

        if is_opt_out(latest_inbound_text):
            log_event(logger, 'sms.ai.opt_out_detected', conversation_id=conversation.id)
            reply = 'Understood — I’ll remove you from my outreach list. Take care.'
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation, lead_status='dnc', dnc_alert=True)
            _log_status_change(session, conversation, status_before, 'dnc')
            send_and_store_message(session, conversation, reply, 'ai.reply')
            log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                      outcome='opt_out_acknowledged')
            return

        history = history_for(session, conversation)
        disposition = bobbie.analyze_conversation_disposition(conversation, history)
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

        if disposition['action'] == 'end':
            closing_context = 'Do not schedule; this conversation is ending.'
            closing_control = (
                f"Final disposition: END. Intent: {disposition['intent']}. Reason: {disposition['reason']}. "
                'Write one brief, respectful closing acknowledgment suited to the owner’s latest message. '
                'Do not ask any question, offer another service, request a call, mention scheduling, or try to continue.'
            )
            closing_reply = bobbie.generate_ai_reply(conversation, history, closing_context, closing_control)
            closing_reply = bobbie.review_and_repair_ai_reply(
                conversation, history, closing_reply, disposition, closing_context, closing_control)
            if not closing_reply or '?' in closing_reply or contains_scheduling_pressure(closing_reply):
                log_event(logger, 'sms.ai.closing.fallback', level=logging.WARNING,
                          conversation_id=conversation.id, reason='unsafe_or_empty_draft')
                closing_reply = 'Understood. Thanks for letting me know, and take care.'
            send_and_store_message(session, conversation, closing_reply, 'ai.closing')
            hand_to_broker(session, conversation)
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
            'answer_and_qualify, answer the concern and ask one concise logical question; do not request a meeting yet. '
            'If it is answer_only, do not ask a question. If it is offer_call, do not qualify further: acknowledge '
            'what is already known and make one low-pressure request to visit the property in person without '
            'proposing a time (a phone call only if the owner has declined a visit or asked for a call). '
            'Never propose, offer or confirm a day or time; a person from the team arranges every meeting.'
        )
        calendar_state = disposition['calendar']
        log_event(logger, 'sms.calendar.state_resolved', conversation_id=conversation.id,
                  source='deepseek', state=calendar_state['state'],
                  should_fetch_availability=calendar_state['should_fetch_availability'])

        scheduling_context = ''
        if calendar_state['should_fetch_availability']:
            # Bobbie never schedules. Once the owner agrees to meet (or names a
            # time), she says a person will follow up and steps back; the
            # assigned broker/agent sets the time and place from Follow-ups.
            reply = 'Great, thank you. Someone from our team will text you shortly to set up a time that works.'
            outbound = send_and_store_message(session, conversation, reply, 'ai.reply')
            previous_status = conversation.lead_status
            hand_to_broker(session, conversation)
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

        fallback_plan = {'next_step': disposition['next_step']}
        reply = bobbie.generate_ai_reply(
            conversation, history, scheduling_context, disposition_control, fallback_plan)

        if contains_unbooked_confirmation(reply) or contains_time_proposal(reply):
            log_event(logger, 'sms.ai.safety_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='unbooked_confirmation')
            reply = 'I haven’t scheduled anything, and I won’t invent a time.'

        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            log_event(logger, 'sms.ai.safety_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='pressure_after_refusal')
            retry_context = (f'{scheduling_context}\nThe previous draft improperly reintroduced a call. '
                             'Reply directly by text without mentioning a call, meeting, schedule, appointment, '
                             'email, or future follow-up.')
            retry = bobbie.generate_ai_reply(conversation, history, retry_context, disposition_control, fallback_plan)
            reply = ('Understood—I’ll keep this to text and answer what I can here.'
                     if contains_scheduling_pressure(retry) else retry)

        reply = bobbie.review_and_repair_ai_reply(conversation, history, reply, disposition,
                                                  scheduling_context, disposition_control)

        if contains_unbooked_confirmation(reply) or contains_time_proposal(reply):
            log_event(logger, 'sms.ai.review_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='calendar_safety')
            reply = 'I haven’t scheduled anything, and I won’t invent a time.'
        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            log_event(logger, 'sms.ai.review_rewrite', level=logging.WARNING,
                      conversation_id=conversation.id, reason='pressure_after_refusal')
            reply = 'Understood—I’ll keep this to text and answer what I can here.'

        outbound = send_and_store_message(session, conversation, reply, 'ai.reply')
        if is_conversation_closing(reply):
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation)
            log_event(logger, 'sms.ai.autopilot_stopped', conversation_id=conversation.id,
                      reason='closing_reply')
        log_event(logger, 'sms.ai.completed', conversation_id=conversation.id,
                  outcome='reply_sent', message_id=outbound.id,
                  text_chars=len(reply))
    except AiUnavailableError as error:
        # Nothing is sent when the model is unreachable; autopilot stays on so the
        # next inbound message retries once DeepSeek is configured again.
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
