"""SMS orchestration — port of the server.js message/AI pipeline.

Everything here is scoped to a single Conversation row, which belongs to exactly
one broker (`Conversation.user_id`), so two brokers texting the same number keep
separate threads.
"""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

import requests

from ..core.config import settings
from ..db import SessionLocal
from ..logger import get_logger
from ..models import Conversation, Message
from . import bobbie, calendar_client
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
    logger.info('[sms-simulation] queued message to %s: %s', to_number, text[:80])
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
    return parsed


def send_and_store_message(session, conversation: Conversation, text: str,
                           event_type: str = 'message.sent',
                           suppress_auto_reply: bool = False) -> Message:
    lead_context = None
    if conversation.lead_context:
        try:
            lead_context = json.loads(conversation.lead_context)
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
        telnyx_id=(response.get('data') or {}).get('id'),
        created_at=datetime.utcnow(),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


# ---------------------------------------------------------------------------
# Conversation state helpers
# ---------------------------------------------------------------------------

def history_for(session, conversation: Conversation) -> list:
    messages = (session.query(Message)
                .filter(Message.conversation_id == conversation.id)
                .order_by(Message.created_at, Message.id)
                .all())
    return [{'direction': message.direction, 'text': message.text or ''} for message in messages]


def is_conversation_closing(text: str = '') -> bool:
    normalized = re.sub(r'^[^\w]+', '', str(text or '').strip(), flags=re.UNICODE)
    if not normalized or '?' in normalized:
        return False
    return any(pattern.search(normalized) for pattern in CLOSING_PATTERNS)


def update_lead_progress(session, conversation: Conversation, **values) -> None:
    for key, value in values.items():
        setattr(conversation, key, value)
    session.add(conversation)
    session.commit()


def hand_to_broker(session, conversation: Conversation) -> None:
    """Bobbie stops; the broker owns every further reply on this thread.

    Autopilot is switched off and ownership recorded, so an inbound message
    afterwards is left pending for the broker instead of waking Bobbie.
    """
    update_lead_progress(session, conversation, ai_enabled=False, handled_by='broker')
    logger.info('[handover] conversation %s handed to the broker', conversation.id)


def hand_to_bobbie(session, conversation: Conversation) -> None:
    """Broker explicitly gives the thread back to Bobbie."""
    update_lead_progress(session, conversation, ai_enabled=True, handled_by='bobbie')
    logger.info('[handover] conversation %s handed back to Bobbie', conversation.id)


def mark_lead_completed(session, conversation: Conversation, **values) -> None:
    update_lead_progress(
        session, conversation,
        **{
            **values,
            'queue_status': 'completed',
            'lead_status': values.get('lead_status') or finalized_lead_status(conversation.lead_status),
            'processed_at': values.get('processed_at') or datetime.utcnow(),
        },
    )


def record_inbound_classification(session, conversation: Conversation, text: str) -> dict | None:
    classification = classify_lead_message(text)
    if not classification:
        logger.info('[lead-classifier] contact=%s status=unchanged terminal=false', conversation.contact)
        return None
    lead_status = merge_lead_status(conversation.lead_status, classification.get('lead_status'))
    updates = {'lead_status': lead_status}
    if 'dnc_alert' in classification:
        updates['dnc_alert'] = bool(classification['dnc_alert'])
    update_lead_progress(session, conversation, **updates)
    logger.info('[lead-classifier] contact=%s status=%s terminal=%s dnc=%s', conversation.contact,
                lead_status, bool(classification.get('terminal')), bool(classification.get('dnc_alert')))
    return {**classification, 'lead_status': lead_status}


# ---------------------------------------------------------------------------
# The AI reply pipeline
# ---------------------------------------------------------------------------

def process_ai_reply(conversation_id: int, latest_inbound_text: str) -> None:
    """Port of processAiReply(). Runs in a background thread with its own session."""
    session = SessionLocal()
    try:
        conversation = session.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation or not conversation.ai_enabled or conversation.handled_by != 'bobbie':
            return

        if is_opt_out(latest_inbound_text):
            reply = 'Understood — I’ll remove you from my outreach list. Take care.'
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation, lead_status='dnc', dnc_alert=True)
            send_and_store_message(session, conversation, reply, 'ai.reply')
            logger.info('AI reply sent to %s', conversation.contact)
            return

        history = history_for(session, conversation)
        disposition = bobbie.analyze_conversation_disposition(conversation, history)

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
                logger.warning('[ai-disposition] contact=%s invalid_closing=true fallback=brief_close',
                               conversation.contact)
                closing_reply = 'Understood. Thanks for letting me know, and take care.'
            send_and_store_message(session, conversation, closing_reply, 'ai.closing')
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation, lead_status=disposition['lead_status'])
            logger.info('[ai-disposition] contact=%s conversation=closed intent=%s',
                        conversation.contact, disposition['intent'])
            return

        continued_status = (disposition['lead_status']
                            if conversation.lead_status in ('not_interested', 'no_response')
                            else merge_lead_status(conversation.lead_status, disposition['lead_status']))
        update_lead_progress(session, conversation, lead_status=continued_status, processed_at=None)

        disposition_control = (
            f"Final disposition: CONTINUE. Intent: {disposition['intent']}. "
            f"Conversation stage: {disposition['conversation_stage']}. "
            f"Required next step: {disposition['next_step']}. "
            f"Qualification focus: {disposition['qualification_focus'] or 'none supplied'}. "
            f"Calendar state: {disposition['calendar']['state']}. Reason: {disposition['reason']}. "
            'Follow the required next step naturally without assuming facts. If the next step is '
            'answer_and_qualify, answer the concern and ask one concise logical question; do not request a call yet. '
            'If it is answer_only, do not ask a question. If it is offer_call, do not qualify further: acknowledge '
            'what is already known and make one low-pressure 5-10 minute call request without proposing a time. '
            'Do not introduce calendar times unless calendar state explicitly permits it.'
        )
        calendar_state = disposition['calendar']
        logger.info('[calendar-state] %s: %s source=deepseek reason=%s',
                    conversation.contact, calendar_state['state'], calendar_state['reason'])

        scheduling_context = ''
        scheduling_fallback_reply = ''
        last_availability = None

        if calendar_state['should_fetch_availability']:
            try:
                availability = calendar_client.fetch_availability(session, conversation.user_id)
                last_availability = availability
                scheduling_context = (
                    f"{calendar_client.prompt_context(availability)}\n"
                    f"Calendar state from DeepSeek: {calendar_state['state']}. "
                    'No booking exists unless the booking API succeeds.'
                )
                if calendar_state['state'] == 'call_accepted':
                    scheduling_fallback_reply = calendar_client.safe_available_offer(availability)
                    logger.info('[calendar-tool] DeepSeek confirmed call consent; offering live slots to %s: %s',
                                conversation.contact, scheduling_fallback_reply)
                else:
                    action = bobbie.resolve_calendar_action(conversation, history, availability, calendar_state)
                    logger.info('[calendar-tool] resolved action for %s: %s', conversation.contact, action)
                    consent_confirmed = (calendar_state['state'] == 'booking_confirmed'
                                         or action.get('consent') in ('offered_slot_selected', 'confirmed_exact'))
                    if action.get('action') == 'book' and consent_confirmed:
                        try:
                            booking = calendar_client.create_booking(conversation, action, session)
                            if booking.get('confirmation_sent') is not True and booking.get('message'):
                                # In-process bookings always land here: the calendar
                                # creates the row, this pipeline sends the confirmation
                                # SMS (suppressing any simulated reply to it).
                                logger.info('[calendar-tool] sending booking confirmation to %s',
                                            conversation.contact)
                                send_and_store_message(session, conversation, booking['message'],
                                                       'calendar.confirmation', True)
                            hand_to_broker(session, conversation)
                            mark_lead_completed(session, conversation, lead_status='ready_to_sell',
                                                meeting_booked=True)
                            logger.info('Meeting booked for %s: %s', conversation.contact, booking.get('start_at'))
                            return
                        except calendar_client.SlotTakenError:
                            refreshed = calendar_client.fetch_availability(session, conversation.user_id)
                            last_availability = refreshed
                            scheduling_context = calendar_client.prompt_context(
                                refreshed,
                                'The requested slot was just taken. Apologize briefly and offer one or two '
                                'nearby available alternatives.')
                            scheduling_fallback_reply = (
                                f'That time was just taken. {calendar_client.safe_available_offer(refreshed)}')
                            logger.info('[calendar-tool] collision alternatives for %s: %s',
                                        conversation.contact, scheduling_fallback_reply)
                    elif action.get('action') == 'ask_confirmation':
                        label = calendar_client._short_label(action.get('label') or '')
                        scheduling_fallback_reply = f'{label} is open. Should I book it for our quick call?'
                        logger.info('[calendar-tool] asking final booking confirmation for %s: %s',
                                    conversation.contact, scheduling_fallback_reply)
                    elif action.get('action') == 'offer_alternatives':
                        scheduling_fallback_reply = (
                            f"That time isn’t open. {calendar_client.safe_available_offer(availability)}")
                        logger.info('[calendar-tool] requested time unavailable for %s: %s',
                                    conversation.contact, scheduling_fallback_reply)
                    elif action.get('action') == 'resolution_failed':
                        scheduling_fallback_reply = ('I couldn’t verify that calendar selection just now. '
                                                     'Please resend the exact day and time you chose.')
                        logger.warning('[calendar-tool] resolver unavailable for %s; no availability claim made',
                                       conversation.contact)
            except Exception as error:
                logger.error('Calendar scheduling unavailable for %s: %s', conversation.contact, error)
                scheduling_context = ('The live calendar is currently unavailable. Do not claim availability or a '
                                      'booking, and do not invent or repeat a time.')
        elif calendar_state['state'] == 'call_declined':
            scheduling_context = ('The recipient declined or deferred a call. Answer their latest question directly. '
                                  'Do not offer times, ask for a call, or pressure them to schedule.')

        fallback_plan = {'next_step': disposition['next_step']}
        reply = scheduling_fallback_reply or bobbie.generate_ai_reply(
            conversation, history, scheduling_context, disposition_control, fallback_plan)

        if contains_unbooked_confirmation(reply) or (not last_availability and contains_time_proposal(reply)):
            logger.warning('[calendar-tool] blocked unbooked confirmation for %s: %s', conversation.contact, reply)
            if last_availability and calendar_state['should_fetch_availability']:
                reply = calendar_client.safe_available_offer(last_availability)
            elif calendar_state['state'] == 'call_accepted':
                reply = 'The live calendar is unavailable, so I can’t offer a verified time right now.'
            else:
                reply = 'I haven’t scheduled anything, and I won’t invent a time.'

        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            logger.warning('[calendar-state] blocked scheduling pressure after refusal for %s: %s',
                           conversation.contact, reply)
            retry_context = (f'{scheduling_context}\nThe previous draft improperly reintroduced a call. '
                             'Reply directly by text without mentioning a call, meeting, schedule, appointment, '
                             'email, or future follow-up.')
            retry = bobbie.generate_ai_reply(conversation, history, retry_context, disposition_control, fallback_plan)
            reply = ('Understood—I’ll keep this to text and answer what I can here.'
                     if contains_scheduling_pressure(retry) else retry)

        reply = bobbie.review_and_repair_ai_reply(conversation, history, reply, disposition,
                                                  scheduling_context, disposition_control)

        if contains_unbooked_confirmation(reply) or (not last_availability and contains_time_proposal(reply)):
            logger.warning('[calendar-tool] reviewer rewrite violated calendar safety for %s: %s',
                           conversation.contact, reply)
            reply = (calendar_client.safe_available_offer(last_availability)
                     if last_availability and calendar_state['should_fetch_availability']
                     else 'I can’t verify a calendar time right now, so I won’t suggest or confirm one.')
        if calendar_state['state'] == 'call_declined' and contains_scheduling_pressure(reply):
            logger.warning('[calendar-state] reviewer rewrite reintroduced scheduling after refusal for %s',
                           conversation.contact)
            reply = 'Understood—I’ll keep this to text and answer what I can here.'

        send_and_store_message(session, conversation, reply, 'ai.reply')
        if is_conversation_closing(reply):
            hand_to_broker(session, conversation)
            mark_lead_completed(session, conversation)
            logger.info('Bobbie closed the conversation with %s; AI autopilot stopped', conversation.contact)
        logger.info('AI reply sent to %s', conversation.contact)
    except AiUnavailableError as error:
        # Nothing is sent when the model is unreachable; autopilot stays on so the
        # next inbound message retries once DeepSeek is configured again.
        logger.error('[bobbie] no reply sent for conversation %s — AI unavailable: %s', conversation_id, error)
    except SmsDeliveryError as error:
        logger.error('[bobbie] reply generated but delivery failed for conversation %s: %s',
                     conversation_id, error)
    except Exception as error:
        logger.error('AI reply failed for conversation %s: %s', conversation_id, error)
    finally:
        session.close()
