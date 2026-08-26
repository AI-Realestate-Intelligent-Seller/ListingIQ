"""Bobbie's DeepSeek runtime — port of ai-runtime.js.

Four model layers, each with a deterministic fallback:
  analyze_conversation_disposition -> structured next-step/calendar decision
  generate_ai_reply                -> the SMS draft, with tool calling + policy repair
  resolve_calendar_action          -> maps a scheduling turn onto live availability
  review_and_repair_ai_reply       -> independent semantic QA of the draft
"""

import json
import re

from ..logger import get_logger
from . import knowledge
from .classifier import classify_lead_message
from .deepseek import AiUnavailableError, completion
from .policy import (
    clean_reply,
    find_reply_policy_violations,
    fit_complete_sms,
    safe_grounded_fallback,
)
from .prompt import build_bobbie_prompt

logger = get_logger(__name__)

TOOLS = [
    {
        'type': 'function',
        'function': {
            'name': 'get_lead_details',
            'description': 'Get the approved property row and outreach reason for this phone number.',
            'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        },
    },
    {
        'type': 'function',
        'function': {
            'name': 'search_bobbie_knowledge',
            'description': 'Search approved Bobbie Fisher and RE/MAX knowledge before stating facts about Bobbie.',
            'parameters': {
                'type': 'object',
                'properties': {'query': {'type': 'string'}},
                'required': ['query'],
                'additionalProperties': False,
            },
        },
    },
]

CONTINUE_STATUSES = {'processing', 'want_more_info', 'interested', 'ready_to_sell'}
LEAD_STATUSES = CONTINUE_STATUSES | {'not_interested'}
NEXT_STEPS = {'answer_and_qualify', 'answer_only', 'offer_call', 'schedule', 'close'}
CALENDAR_STATES = {'none', 'call_declined', 'call_accepted', 'time_proposed', 'booking_confirmed'}
CONVERSATION_STAGES = {'discovery', 'qualified_for_call', 'call_requested', 'scheduling', 'complete'}
OUTCOMES = {'positive', 'neutral', 'negative', 'disqualified'}

DISPOSITION_SYSTEM = """You are the conversation disposition and next-step layer for Bobbie's property-owner SMS chat. Decide whether there is any reasonable path to continue and what Bobbie should do next.
Return only JSON: {"action":"continue|end","intent":"short_snake_case","outcome":"positive|neutral|negative|disqualified","lead_status":"processing|want_more_info|interested|ready_to_sell|not_interested","confidence":0.0,"conversation_stage":"discovery|qualified_for_call|call_requested|scheduling|complete","next_step":"answer_and_qualify|answer_only|offer_call|schedule|close","qualification_focus":"brief topic or empty","calendar":{"state":"none|call_declined|call_accepted|time_proposed|booking_confirmed","should_fetch_availability":false,"requested_time_text":"exact owner words or empty","reason":"brief"},"reason":"brief"}.
Choose continue when the owner asks a question, requests information, states a condition, raises an objection that can be answered, expresses hesitation, says maybe/later, rejects only a call or listing method, or otherwise leaves any opening. Treat conditional willingness as continue even when the message contains words like "not interested."
Choose end only when the owner clearly wants the conversation to stop, clearly rejects both selling and further discussion without any question or condition, reports a final disqualifier such as wrong number/already sold, or gives a pure closing after the matter is resolved. Action controls whether messaging continues; it does not determine lead quality. A polite goodbye after the owner said they are ready to sell must remain positive/ready_to_sell, never not_interested. Use negative/not_interested only for an actual rejection. When uncertain, choose continue.
For a new objection, condition, failed-listing concern, or unclear motivation, use discovery plus answer_and_qualify and identify the single most logical missing detail. Do not ask for information the owner already clearly provided.
Use qualified_for_call plus offer_call as soon as the owner is willing or conditionally willing to sell and Bobbie knows at least one useful decision detail such as motivation, desired outcome/price, timing, property situation, or the condition under which they would sell. One or two useful qualification exchanges are enough. Do not keep interviewing for contact details, documents, title/liens, closing logistics, or repeat confirmation of willingness before requesting the call. If the owner asks a direct question at this stage, answer it briefly and invite the call in the same SMS.
Use call_requested only when Bobbie's latest message already invited a call and the owner has not accepted or declined it. Use scheduling only after explicit call acceptance or time discussion. Never mark discovery once the owner has already provided sufficient selling intent and a useful decision detail.
Calendar state must be based on the latest conversational turn, not isolated keywords. A short answer such as "yes" answers Bobbie's immediately preceding question only; never attach it to an older call invitation. Use call_accepted only when the owner accepts Bobbie's current call request. Use time_proposed when the owner proposes a new day/time that Bobbie did not offer; after availability is verified Bobbie must ask whether to book that exact slot. Use booking_confirmed when either (a) the owner selects one exact slot from Bobbie's immediately preceding live-calendar options, or (b) Bobbie asks to book one exact verified slot and the owner clearly agrees. Selecting an offered option is sufficient consent and must book immediately; do not ask for another confirmation. Otherwise use none. Do not invent hidden intent."""

REVIEW_SYSTEM = """You are an independent semantic QA reviewer for Bobbie's property-owner SMS. Determine whether the draft is the response the owner would reasonably expect next.
Return only JSON: {"valid":true,"issues":["short_issue"],"rewrite_instruction":"specific instruction or empty","reason":"brief"}.
A valid reply must answer the owner's latest message as a whole, follow the supplied disposition and required next step, make sense after the immediately preceding turn, avoid repeating answered questions, avoid unsupported claims, and avoid jumping to a call or calendar unless the supplied calendar state permits it. When next_step is offer_call, another qualification question without a low-pressure call invitation is invalid. Any promise to book or confirm later is invalid: a booking claim may only come from the calendar API after creation succeeds. Selecting one of Bobbie's immediately preceding live slots is already booking consent and should produce the real calendar confirmation, not a conversational placeholder. A bare "yes" answers the immediately preceding question, not an older invitation. Be strict about non sequiturs, over-qualification, and premature scheduling, but do not reject concise natural SMS merely for style."""


def _json_object(content: str) -> dict:
    match = re.search(r'\{[\s\S]*\}', str(content or ''))
    if not match:
        raise ValueError('Model returned no JSON object')
    return json.loads(match.group(0))


def _latest_inbound(history: list) -> str:
    for item in reversed(history):
        if item.get('direction') == 'inbound':
            return item.get('text') or ''
    return ''


def generate_no_response_followup(conversation, history: list, attempt: int, maximum: int) -> str:
    """Write one low-pressure follow-up using the stored thread as context."""
    address = conversation.property_address or 'the property'
    fallbacks = {
        1: f'Just circling back—did you happen to see my earlier text about {address}?',
        2: f'I wanted to follow up once more about {address}. Is selling something you would consider?',
        maximum: ('Just closing the loop. If selling is still something you’d consider, feel free to reply. '
                  'Otherwise, I won’t follow up again.'),
    }
    fallback = fallbacks.get(attempt, fallbacks.get(maximum))
    recent = [{'role': 'Bobbie' if item.get('direction') == 'outbound' else 'Owner',
               'text': str(item.get('text') or '')} for item in history[-12:]]
    final = attempt >= maximum
    instruction = (
        'Write Bobbie’s final follow-up SMS. Say this is the last outreach in a calm, respectful way, invite a '
        'reply if selling is still relevant, and say there will be no more follow-ups. Do not threaten or pressure.'
        if final else
        f'Write follow-up SMS {attempt} of {maximum} because the owner has not replied. Briefly and naturally ask '
        'whether they saw the earlier property text. Do not claim urgency, invent facts, or pressure them.'
    )
    try:
        result = completion([
            {'role': 'system', 'content': (
                'You write concise property-owner SMS messages for Bobbie. Use the supplied chat history, avoid '
                'repeating an earlier follow-up verbatim, use at most 220 characters, and output only the SMS.')},
            {'role': 'user', 'content': json.dumps({
                'property': address, 'attempt': attempt, 'maximum_attempts': maximum,
                'recent_conversation': recent, 'instruction': instruction,
            })},
        ], temperature=0.25, max_tokens=90)
        message = clean_reply(result.get('content') or '')
        return fit_complete_sms(message, 240) if message else fallback
    except AiUnavailableError as error:
        logger.warning('[followup-ai] contact=%s attempt=%s fallback=true error=%s',
                       conversation.contact, attempt, error)
        return fallback


# ---------------------------------------------------------------------------
# Disposition
# ---------------------------------------------------------------------------

def fallback_disposition(latest_inbound: str, error: Exception | None = None) -> dict:
    classification = classify_lead_message(latest_inbound) or {}
    should_end = bool(classification.get('terminal'))
    lead_status = classification.get('lead_status') or 'unclear'
    return {
        'action': 'end' if should_end else 'continue',
        'intent': lead_status,
        'lead_status': 'not_interested' if should_end else (
            lead_status if lead_status in CONTINUE_STATUSES else 'processing'),
        'outcome': 'negative' if should_end else 'neutral',
        'confidence': 0,
        'next_step': 'close' if should_end else 'answer_and_qualify',
        'qualification_focus': '',
        'conversation_stage': 'complete' if should_end else 'discovery',
        'calendar': {
            'state': 'none',
            'should_fetch_availability': False,
            'requested_time_text': '',
            'reason': 'AI disposition unavailable; calendar remains inactive',
        },
        'reason': (f'Disposition model unavailable; safe fallback used: {error}'
                   if error else 'Safe fallback used'),
    }


def analyze_conversation_disposition(conversation, history: list) -> dict:
    recent = [
        {'role': 'Bobbie' if item.get('direction') == 'outbound' else 'Owner',
         'text': str(item.get('text') or '')}
        for item in history[-16:]
    ]
    latest_inbound = _latest_inbound(history)
    try:
        assistant = completion(
            [
                {'role': 'system', 'content': DISPOSITION_SYSTEM},
                {'role': 'user', 'content': json.dumps(
                    {'property': conversation.property_address or None, 'recent_conversation': recent})},
            ],
            temperature=0,
            max_tokens=240,
        )
        parsed = _json_object(assistant.get('content'))
        action = parsed.get('action') if parsed.get('action') in ('continue', 'end') else None
        if not action:
            raise ValueError('Disposition model returned an invalid action')

        requested_status = str(parsed.get('lead_status') or 'processing')
        stored_status = conversation.lead_status if conversation.lead_status in LEAD_STATUSES else 'processing'
        outcome = parsed.get('outcome') if parsed.get('outcome') in OUTCOMES else 'neutral'
        lead_status = requested_status if requested_status in LEAD_STATUSES else stored_status
        if (action == 'end' and outcome not in ('negative', 'disqualified')
                and stored_status != 'processing' and stored_status in CONTINUE_STATUSES):
            lead_status = (requested_status
                           if requested_status in CONTINUE_STATUSES and requested_status != 'processing'
                           else stored_status)

        calendar_in = parsed.get('calendar') or {}
        requested_calendar_state = str(calendar_in.get('state') or 'none')
        calendar_state = ('none' if action == 'end' or requested_calendar_state not in CALENDAR_STATES
                          else requested_calendar_state)
        requested_stage = str(parsed.get('conversation_stage') or 'discovery')
        conversation_stage = 'complete' if action == 'end' else (
            requested_stage if requested_stage in CONVERSATION_STAGES and requested_stage != 'complete'
            else 'discovery')
        next_step = 'close' if action == 'end' else (
            parsed.get('next_step') if parsed.get('next_step') in NEXT_STEPS and parsed.get('next_step') != 'close'
            else 'answer_and_qualify')
        # DeepSeek owns the semantic readiness decision. Once its structured output
        # says qualification is sufficient, prevent another unnecessary interview
        # question and transition to a low-pressure call invitation.
        if action == 'continue' and conversation_stage == 'qualified_for_call' and calendar_state == 'none':
            next_step = 'offer_call'

        try:
            confidence = max(0.0, min(1.0, float(parsed.get('confidence') or 0)))
        except (TypeError, ValueError):
            confidence = 0.0

        result = {
            'action': action,
            'intent': str(parsed.get('intent') or 'unclear')[:80],
            'outcome': outcome,
            'lead_status': lead_status,
            'confidence': confidence,
            'conversation_stage': conversation_stage,
            'next_step': next_step,
            'qualification_focus': '' if next_step == 'offer_call' else str(parsed.get('qualification_focus') or '')[:160],
            'calendar': {
                'state': calendar_state,
                'should_fetch_availability': calendar_state in ('call_accepted', 'time_proposed', 'booking_confirmed'),
                'requested_time_text': str(calendar_in.get('requested_time_text') or '')[:160],
                'reason': str(calendar_in.get('reason') or '')[:200],
            },
            'reason': str(parsed.get('reason') or '')[:240],
        }
        logger.info(
            '[ai-disposition] contact=%s action=%s intent=%s outcome=%s stage=%s next_step=%s calendar=%s status=%s confidence=%.2f reason=%s',
            conversation.contact, result['action'], result['intent'], result['outcome'],
            result['conversation_stage'], result['next_step'], result['calendar']['state'],
            result['lead_status'], result['confidence'], result['reason'],
        )
        return result
    except (AiUnavailableError, ValueError, json.JSONDecodeError, KeyError, TypeError) as error:
        fallback = fallback_disposition(latest_inbound, error)
        logger.warning('[ai-disposition] contact=%s status=fallback action=%s intent=%s error=%s',
                       conversation.contact, fallback['action'], fallback['intent'], error)
        return fallback


# ---------------------------------------------------------------------------
# Approved runtime context
# ---------------------------------------------------------------------------

def approved_lead_details(conversation) -> dict:
    lead = {'available': False}
    if conversation.lead_context:
        try:
            lead = json.loads(conversation.lead_context)
        except (ValueError, TypeError):
            lead = {'available': False, 'error': 'Stored lead context is invalid'}
    phone_source = lead.get('phone_number_source') or lead.get('contact_source')
    return {
        **lead,
        'phone_number_source': phone_source or {
            'available': False,
            'safe_response': (
                'The imported lead row does not identify how the phone number was sourced. Do not claim it came '
                'from public, property, listing, county, or tax records.'
            ),
        },
        'communication_capabilities': {
            'send_email': False,
            'retrieve_live_comps': False,
            'verify_recent_sales': False,
            'perform_future_follow_up': False,
            'provide_callback_number': False,
            'send_sms_now': True,
        },
        'verified_buyer': lead.get('verified_buyer') or {
            'available': False,
            'safe_response': ('No specific buyer for this property is verified in the approved context. '
                              'Say that plainly if asked.'),
        },
    }


def _approved_runtime_context(conversation, history: list) -> dict:
    latest_inbound = _latest_inbound(history)
    lead = approved_lead_details(conversation)
    knowledge_result = {
        'available': False,
        'reason': 'The latest message does not require a Bobbie knowledge lookup.',
    }
    if knowledge.needs_bobbie_knowledge(latest_inbound):
        logger.info('[ai-tool] contact=%s tool=search_bobbie_knowledge status=started mode=preload',
                    conversation.contact)
        try:
            knowledge_result = {'available': True, **knowledge.search_bobbie_knowledge(latest_inbound, 3)}
        except Exception as error:  # network/service failure must not break the reply
            knowledge_result = {'available': False, 'error': str(error)}
            logger.warning('[ai-tool] contact=%s tool=search_bobbie_knowledge status=failed error=%s',
                           conversation.contact, error)
    return {'latest_inbound': latest_inbound, 'lead': lead, 'knowledge': knowledge_result}


# ---------------------------------------------------------------------------
# Reply generation
# ---------------------------------------------------------------------------

def generate_ai_reply(conversation, history: list, scheduling_context: str = '',
                      disposition_control: str = '', fallback_plan: dict | None = None) -> str:
    fallback_plan = fallback_plan or {}
    approved = _approved_runtime_context(conversation, history)
    messages = [
        {'role': 'system', 'content': build_bobbie_prompt(conversation, scheduling_context)},
        {'role': 'system', 'content': 'Approved runtime context (facts only): ' + json.dumps(
            {'lead': approved['lead'], 'bobbie_knowledge': approved['knowledge']})},
    ]
    if disposition_control:
        messages.append({'role': 'system', 'content': disposition_control})
    messages.extend(
        {'role': 'assistant' if item.get('direction') == 'outbound' else 'user', 'content': item.get('text') or ''}
        for item in history[-30:] if item.get('text')
    )

    content = ''
    for _ in range(3):
        assistant = completion(messages, tools=TOOLS, tool_choice='auto', temperature=0.4, max_tokens=120)
        calls = assistant.get('tool_calls') or []
        if not calls:
            content = assistant.get('content') or ''
            break
        messages.append({'role': 'assistant', 'content': assistant.get('content') or '', 'tool_calls': calls})
        for call in calls:
            name = (call.get('function') or {}).get('name') or 'unknown'
            logger.info('[ai-tool] contact=%s tool=%s status=started', conversation.contact, name)
            try:
                if name == 'get_lead_details':
                    result = approved['lead']
                elif name == 'search_bobbie_knowledge':
                    arguments = json.loads((call.get('function') or {}).get('arguments') or '{}')
                    result = knowledge.search_bobbie_knowledge(str(arguments.get('query') or '')[:500], 4)
                else:
                    result = {'error': 'Unknown tool'}
            except Exception as error:
                result = {'error': str(error)}
            messages.append({'role': 'tool', 'tool_call_id': call.get('id'), 'content': json.dumps(result)})
            logger.info('[ai-tool] contact=%s tool=%s status=completed', conversation.contact, name)

    reply = clean_reply(content)
    if not reply:
        logger.warning('[ai-policy] contact=%s draft=empty retrying=once', conversation.contact)
        try:
            retried = completion(
                messages + [{'role': 'system', 'content': (
                    'Return one non-empty, complete Bobbie SMS now. Use the approved context and answer the '
                    'latest message in at most 220 characters.')}],
                temperature=0.1, max_tokens=80,
            )
            reply = clean_reply(retried.get('content') or '')
        except AiUnavailableError as error:
            logger.warning('[ai-policy] contact=%s empty-retry=failed error=%s', conversation.contact, error)
        if not reply:
            reply = safe_grounded_fallback(approved['latest_inbound'], [], history, fallback_plan)

    phone_source_known = bool((approved['lead'].get('phone_number_source') or {}).get('available'))
    violations = find_reply_policy_violations(reply, phone_source_known=phone_source_known,
                                              max_length=240, history=history)
    if violations:
        logger.warning('[ai-policy] contact=%s draft=blocked violations=%s',
                       conversation.contact, ','.join(violations))
        correction = (
            'Rewrite Bobbie\'s answer from scratch in at most 220 characters. Fix these violations: '
            f"{', '.join(violations)}. Use only the approved runtime context, answer the latest owner question "
            'first, and output one complete SMS.'
        )
        repaired_reply = ''
        try:
            repaired = completion(messages + [{'role': 'system', 'content': correction}],
                                  temperature=0.1, max_tokens=80)
            repaired_reply = clean_reply(repaired.get('content') or '')
        except AiUnavailableError as error:
            logger.warning('[ai-policy] contact=%s repair=failed error=%s', conversation.contact, error)
        repaired_violations = find_reply_policy_violations(
            repaired_reply, phone_source_known=phone_source_known, max_length=240, history=history)
        if repaired_reply and not repaired_violations:
            reply, violations = repaired_reply, []
            logger.info('[ai-policy] contact=%s draft=repaired', conversation.contact)
        else:
            violations = repaired_violations or violations
            reply = safe_grounded_fallback(approved['latest_inbound'], violations, history, fallback_plan)
            logger.warning('[ai-policy] contact=%s fallback=grounded violations=%s',
                           conversation.contact, ','.join(violations))
    return fit_complete_sms(reply, 240)


# ---------------------------------------------------------------------------
# Calendar resolution
# ---------------------------------------------------------------------------

def _normalized_schedule_text(value: str = '') -> str:
    text = re.sub(r'\b0+(\d)\b', r'\1', str(value or '').lower())
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def _slot_label_parts(label: str = '') -> dict | None:
    match = re.match(
        r'^([^,]+),\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s+at\s+(\d{1,2}):([0-5]\d)\s+(AM|PM)',
        str(label or ''), re.I)
    if not match:
        return None
    return {
        'weekday': match.group(1).lower(),
        'month': match.group(2).lower(),
        'day': str(int(match.group(3))),
        'hour': str(int(match.group(5))),
        'minute': match.group(6),
        'period': match.group(7).lower(),
    }


def exact_offered_slot(history: list, availability: dict) -> dict | None:
    """Match a bare owner time reply against the slots Bobbie just offered."""
    latest_inbound_index = -1
    for index in range(len(history) - 1, -1, -1):
        if history[index].get('direction') == 'inbound':
            latest_inbound_index = index
            break
    if latest_inbound_index < 1 or history[latest_inbound_index - 1].get('direction') != 'outbound':
        return None

    owner_text = _normalized_schedule_text(history[latest_inbound_index].get('text') or '')
    bobbie_text = _normalized_schedule_text(history[latest_inbound_index - 1].get('text') or '')
    owner_time = re.search(r'\b(1[0-2]|[1-9])(?:\s+([0-5]\d))?\s+(am|pm)\b', owner_text)
    if not owner_time:
        return None
    owner_clock = f"{int(owner_time.group(1))}:{owner_time.group(2) or '00'}:{owner_time.group(3)}"

    offered = []
    for slot in (availability.get('slots') or []):
        parts = _slot_label_parts(slot.get('label'))
        if not parts:
            continue
        full_label = _normalized_schedule_text(
            f"{parts['weekday']} {parts['month']} {parts['day']} at {parts['hour']} {parts['minute']} {parts['period']}")
        date_and_time = _normalized_schedule_text(
            f"{parts['month']} {parts['day']} at {parts['hour']} {parts['minute']} {parts['period']}")
        if full_label in bobbie_text or date_and_time in bobbie_text:
            offered.append(slot)

    time_matches = []
    for slot in offered:
        parts = _slot_label_parts(slot.get('label'))
        if parts and f"{parts['hour']}:{parts['minute']}:{parts['period']}" == owner_clock:
            time_matches.append(slot)
    if not time_matches:
        return None

    dated_matches = []
    for slot in time_matches:
        parts = _slot_label_parts(slot.get('label'))
        if f"{parts['month']} {parts['day']}" in owner_text or parts['weekday'] in owner_text:
            dated_matches.append(slot)
    matches = dated_matches or time_matches
    return matches[0] if len(matches) == 1 else None


def resolve_calendar_action(conversation, history: list, availability: dict,
                            calendar_decision: dict | None = None) -> dict:
    calendar_decision = calendar_decision or {}
    state = calendar_decision.get('state') or 'none'
    logger.info('[calendar-tool] DeepSeek calendar resolution started for %s state=%s', conversation.contact, state)
    slots = availability.get('slots') or []
    recovered_slot = exact_offered_slot(history, availability)
    if state == 'booking_confirmed' and recovered_slot:
        logger.info('[calendar-tool] exact offered-slot selection resolved locally for %s: %s',
                    conversation.contact, recovered_slot.get('start_at'))
        return {'action': 'book', 'consent': 'offered_slot_selected', **recovered_slot,
                'reason': "Exact selection matched Bobbie's immediately preceding live-calendar options"}

    slot_lines = '\n'.join(f"{slot.get('start_at')}|{slot.get('end_at')}|{slot.get('label')}" for slot in slots)
    system = f"""Resolve the owner's scheduling turn against live availability. Disposition calendar state: {state}; requested words: {calendar_decision.get('requested_time_text') or ''}; current time: {availability.get('current_time')}; timezone: {availability.get('timezone')}.
Available slots:
{slot_lines}
Return only JSON: {{"action":"none|offer_alternatives|ask_confirmation|book","consent":"none|owner_proposed|offered_slot_selected|confirmed_exact","start_at":null,"end_at":null,"reason":"brief"}}.
Independently compare Bobbie's immediately preceding message with the owner's latest reply. Use offered_slot_selected only when Bobbie offered that exact slot as a live option and the owner selected it. Use owner_proposed when the owner introduced a day/time Bobbie had not offered. Use confirmed_exact when Bobbie asked to book one exact verified slot and the owner agreed.
For time_proposed: choose ask_confirmation only when one exact owner-proposed slot is available; otherwise offer_alternatives. Never book an owner-proposed time until Bobbie has verified it and asked for confirmation.
For booking_confirmed: choose book when the owner either selected one exact slot from Bobbie's immediately preceding available options or confirmed Bobbie's request to book one exact verified slot. Selecting an offered option is already confirmation; do not ask again. Copy timestamps exactly. Otherwise choose none or offer_alternatives.
For call_accepted choose none because the server will offer live slots. Never infer consent from an older question."""

    base_messages = [{'role': 'system', 'content': system}] + [
        {'role': 'assistant' if item.get('direction') == 'outbound' else 'user', 'content': item.get('text') or ''}
        for item in history[-14:]
    ]

    last_error = None
    for attempt in (1, 2):
        try:
            messages = base_messages if attempt == 1 else base_messages + [{
                'role': 'system',
                'content': ('Your previous response was empty or invalid. Return the requested JSON object now, '
                            'with no prose or markdown.'),
            }]
            options = {'temperature': 0, 'max_tokens': 180}
            if attempt == 1:
                options['response_format'] = {'type': 'json_object'}
            assistant = completion(messages, **options)
            decision = _json_object(assistant.get('content'))
            slot = next((item for item in slots
                         if item.get('start_at') == decision.get('start_at')
                         and item.get('end_at') == decision.get('end_at')), None)
            requested_action = (decision.get('action')
                                if decision.get('action') in ('none', 'offer_alternatives', 'ask_confirmation', 'book')
                                else 'none')
            consent = (decision.get('consent')
                       if decision.get('consent') in ('none', 'owner_proposed', 'offered_slot_selected', 'confirmed_exact')
                       else 'none')
            booking_consent = (state == 'booking_confirmed'
                               or consent in ('offered_slot_selected', 'confirmed_exact'))
            if requested_action == 'book' and not booking_consent:
                action = 'ask_confirmation'
            elif requested_action == 'ask_confirmation' and booking_consent and slot:
                action = 'book'
            else:
                action = requested_action
            if action in ('ask_confirmation', 'book') and not slot:
                return {'action': 'offer_alternatives', 'consent': consent, 'start_at': None, 'end_at': None,
                        'reason': 'Requested slot is not currently available'}
            return {'action': action, 'consent': consent,
                    **(slot or {'start_at': None, 'end_at': None}),
                    'reason': str(decision.get('reason') or '')[:200]}
        except (AiUnavailableError, ValueError, json.JSONDecodeError, TypeError) as error:
            last_error = error
            logger.warning('[calendar-tool] DeepSeek calendar resolution attempt %s failed for %s: %s',
                           attempt, conversation.contact, error)

    if recovered_slot:
        logger.info('[calendar-tool] resolver fallback matched exact offered slot for %s: %s',
                    conversation.contact, recovered_slot.get('start_at'))
        return {'action': 'book', 'consent': 'offered_slot_selected', **recovered_slot,
                'reason': 'DeepSeek resolver unavailable; exact selection safely matched a live offered slot'}
    return {'action': 'resolution_failed', 'consent': 'none', 'start_at': None, 'end_at': None,
            'reason': f'Calendar interpretation unavailable: {last_error or "unknown error"}'}


# ---------------------------------------------------------------------------
# Independent review
# ---------------------------------------------------------------------------

def review_ai_reply(conversation, history: list, draft: str, disposition: dict,
                    scheduling_context: str = '') -> dict:
    recent = [
        {'role': 'Bobbie' if item.get('direction') == 'outbound' else 'Owner', 'text': str(item.get('text') or '')}
        for item in history[-16:]
    ]
    try:
        assistant = completion(
            [
                {'role': 'system', 'content': REVIEW_SYSTEM},
                {'role': 'user', 'content': json.dumps({
                    'disposition': disposition,
                    'scheduling_context': scheduling_context,
                    'recent_conversation': recent,
                    'proposed_bobbie_reply': draft,
                })},
            ],
            temperature=0, max_tokens=160,
        )
        parsed = _json_object(assistant.get('content'))
        issues = parsed.get('issues')
        result = {
            'valid': parsed.get('valid') is True,
            'issues': [str(issue)[:100] for issue in issues[:6]] if isinstance(issues, list) else [],
            'rewrite_instruction': str(parsed.get('rewrite_instruction') or '')[:400],
            'reason': str(parsed.get('reason') or '')[:240],
        }
        logger.info('[ai-review] contact=%s valid=%s issues=%s reason=%s', conversation.contact,
                    result['valid'], ','.join(result['issues']) or 'none', result['reason'])
        return result
    except (AiUnavailableError, ValueError, json.JSONDecodeError, TypeError) as error:
        logger.warning('[ai-review] contact=%s status=unavailable action=keep_policy_checked_draft error=%s',
                       conversation.contact, error)
        return {'valid': True, 'issues': [], 'rewrite_instruction': '',
                'reason': 'Reviewer unavailable; policy-checked draft retained'}


def review_and_repair_ai_reply(conversation, history: list, draft: str, disposition: dict,
                               scheduling_context: str = '', disposition_control: str = '') -> str:
    review = review_ai_reply(conversation, history, draft, disposition, scheduling_context)
    if review['valid']:
        return draft
    repair_control = (
        f'{disposition_control}\nIndependent reviewer rejected the previous draft. Issues: '
        f"{', '.join(review['issues']) or 'semantic mismatch'}. Rewrite instruction: "
        f"{review['rewrite_instruction'] or review['reason']}. Write a fresh response that fixes these issues; "
        'never defend or mention the previous draft.'
    )
    repaired = generate_ai_reply(conversation, history, scheduling_context, repair_control,
                                 {'next_step': disposition.get('next_step')})
    logger.info('[ai-review] contact=%s action=rewritten', conversation.contact)
    return repaired
