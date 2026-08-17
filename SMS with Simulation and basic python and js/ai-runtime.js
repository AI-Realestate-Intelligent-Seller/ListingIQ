import { searchBobbieKnowledge } from './bobbie-knowledge.js';
import { buildBobbiePrompt } from './bobbie-prompt.js';
import { cleanReply, findReplyPolicyViolations, fitCompleteSms, safeGroundedFallback } from './bobbie-policy.js';
import { classifyLeadMessage } from './lead-classifier.js';

const tools = [
  { type: 'function', function: { name: 'get_lead_details', description: 'Get the approved property row and outreach reason for this phone number.', parameters: { type: 'object', properties: {}, additionalProperties: false } } },
  { type: 'function', function: { name: 'search_bobbie_knowledge', description: 'Search approved Bobbie Fisher and RE/MAX knowledge before stating facts about Bobbie.', parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'], additionalProperties: false } } }
];

function config() {
  const genericKey = String(process.env.AI_API_KEY || '').trim();
  const useDeepSeek = Boolean(process.env.DEEPSEEK_API_KEY) && (!genericKey || /^your[-_]/i.test(genericKey));
  const apiKey = useDeepSeek ? process.env.DEEPSEEK_API_KEY : genericKey;
  if (!apiKey) throw new Error('DEEPSEEK_API_KEY (or AI_API_KEY) is not configured');
  return {
    apiKey,
    baseUrl: (useDeepSeek ? 'https://api.deepseek.com' : (process.env.AI_BASE_URL || 'https://api.deepseek.com')).replace(/\/$/, ''),
    model: useDeepSeek ? 'deepseek-v4-flash' : (process.env.AI_MODEL || 'deepseek-v4-flash')
  };
}

async function completion(messages, options = {}) {
  const { apiKey, baseUrl, model } = config();
  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST', headers: { Authorization: `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ model, messages, thinking: { type: 'disabled' }, stream: false, ...options }),
    signal: AbortSignal.timeout(Number(process.env.AI_REQUEST_TIMEOUT_MS || 30000))
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.error?.message || `AI provider returned HTTP ${response.status}`);
  return payload?.choices?.[0]?.message || {};
}

const CONTINUE_STATUSES = new Set(['processing', 'want_more_info', 'interested', 'ready_to_sell']);
const LEAD_STATUSES = new Set([...CONTINUE_STATUSES, 'not_interested']);
const NEXT_STEPS = new Set(['answer_and_qualify', 'answer_only', 'offer_call', 'schedule', 'close']);
const CALENDAR_STATES = new Set(['none', 'call_declined', 'call_accepted', 'time_proposed', 'booking_confirmed']);
const CONVERSATION_STAGES = new Set(['discovery', 'qualified_for_call', 'call_requested', 'scheduling', 'complete']);

function fallbackDisposition(latestInbound, error = null) {
  const classification = classifyLeadMessage(latestInbound);
  const shouldEnd = Boolean(classification?.terminal);
  return {
    action: shouldEnd ? 'end' : 'continue',
    intent: classification?.lead_status || 'unclear',
    lead_status: shouldEnd ? 'not_interested' : (CONTINUE_STATUSES.has(classification?.lead_status) ? classification.lead_status : 'processing'),
    outcome: shouldEnd ? 'negative' : 'neutral',
    confidence: 0,
    next_step: shouldEnd ? 'close' : 'answer_and_qualify',
    qualification_focus: '',
    conversation_stage: shouldEnd ? 'complete' : 'discovery',
    calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'AI disposition unavailable; calendar remains inactive' },
    reason: error ? `Disposition model unavailable; safe fallback used: ${error.message}` : 'Safe fallback used'
  };
}

async function analyzeConversationDisposition(conversation, history) {
  const recent = history.slice(-16).map(item => ({
    role: item.direction === 'outbound' ? 'Bobbie' : 'Owner',
    text: String(item.text || '')
  }));
  const latestInbound = [...history].reverse().find(item => item.direction === 'inbound')?.text || '';
  const system = `You are the conversation disposition and next-step layer for Bobbie's property-owner SMS chat. Decide whether there is any reasonable path to continue and what Bobbie should do next.
Return only JSON: {"action":"continue|end","intent":"short_snake_case","outcome":"positive|neutral|negative|disqualified","lead_status":"processing|want_more_info|interested|ready_to_sell|not_interested","confidence":0.0,"conversation_stage":"discovery|qualified_for_call|call_requested|scheduling|complete","next_step":"answer_and_qualify|answer_only|offer_call|schedule|close","qualification_focus":"brief topic or empty","calendar":{"state":"none|call_declined|call_accepted|time_proposed|booking_confirmed","should_fetch_availability":false,"requested_time_text":"exact owner words or empty","reason":"brief"},"reason":"brief"}.
Choose continue when the owner asks a question, requests information, states a condition, raises an objection that can be answered, expresses hesitation, says maybe/later, rejects only a call or listing method, or otherwise leaves any opening. Treat conditional willingness as continue even when the message contains words like "not interested."
Choose end only when the owner clearly wants the conversation to stop, clearly rejects both selling and further discussion without any question or condition, reports a final disqualifier such as wrong number/already sold, or gives a pure closing after the matter is resolved. Action controls whether messaging continues; it does not determine lead quality. A polite goodbye after the owner said they are ready to sell must remain positive/ready_to_sell, never not_interested. Use negative/not_interested only for an actual rejection. When uncertain, choose continue.
For a new objection, condition, failed-listing concern, or unclear motivation, use discovery plus answer_and_qualify and identify the single most logical missing detail. Do not ask for information the owner already clearly provided.
Use qualified_for_call plus offer_call as soon as the owner is willing or conditionally willing to sell and Bobbie knows at least one useful decision detail such as motivation, desired outcome/price, timing, property situation, or the condition under which they would sell. One or two useful qualification exchanges are enough. Do not keep interviewing for contact details, documents, title/liens, closing logistics, or repeat confirmation of willingness before requesting the call. If the owner asks a direct question at this stage, answer it briefly and invite the call in the same SMS.
Use call_requested only when Bobbie's latest message already invited a call and the owner has not accepted or declined it. Use scheduling only after explicit call acceptance or time discussion. Never mark discovery once the owner has already provided sufficient selling intent and a useful decision detail.
Calendar state must be based on the latest conversational turn, not isolated keywords. A short answer such as "yes" answers Bobbie's immediately preceding question only; never attach it to an older call invitation. Use call_accepted only when the owner accepts Bobbie's current call request. Use time_proposed when the owner proposes a new day/time that Bobbie did not offer; after availability is verified Bobbie must ask whether to book that exact slot. Use booking_confirmed when either (a) the owner selects one exact slot from Bobbie's immediately preceding live-calendar options, or (b) Bobbie asks to book one exact verified slot and the owner clearly agrees. Selecting an offered option is sufficient consent and must book immediately; do not ask for another confirmation. Otherwise use none. Do not invent hidden intent.`;
  try {
    const assistant = await completion([
      { role: 'system', content: system },
      { role: 'user', content: JSON.stringify({ property: conversation.property_address || null, recent_conversation: recent }) }
    ], { temperature: 0, max_tokens: 240 });
    const match = String(assistant.content || '').match(/\{[\s\S]*\}/);
    if (!match) throw new Error('Disposition model returned no JSON object');
    const parsed = JSON.parse(match[0]);
    const action = parsed.action === 'end' ? 'end' : parsed.action === 'continue' ? 'continue' : null;
    if (!action) throw new Error('Disposition model returned an invalid action');
    const requestedStatus = String(parsed.lead_status || 'processing');
    const storedStatus = LEAD_STATUSES.has(conversation.lead_status) ? conversation.lead_status : 'processing';
    const outcome = ['positive', 'neutral', 'negative', 'disqualified'].includes(parsed.outcome) ? parsed.outcome : 'neutral';
    let leadStatus = LEAD_STATUSES.has(requestedStatus) ? requestedStatus : storedStatus;
    if (action === 'end' && outcome !== 'negative' && outcome !== 'disqualified' && storedStatus !== 'processing' && CONTINUE_STATUSES.has(storedStatus)) {
      leadStatus = CONTINUE_STATUSES.has(requestedStatus) && requestedStatus !== 'processing' ? requestedStatus : storedStatus;
    }
    const requestedCalendarState = String(parsed.calendar?.state || 'none');
    const calendarState = action === 'end' || !CALENDAR_STATES.has(requestedCalendarState) ? 'none' : requestedCalendarState;
    const requestedStage = String(parsed.conversation_stage || 'discovery');
    const conversationStage = action === 'end'
      ? 'complete'
      : (CONVERSATION_STAGES.has(requestedStage) && requestedStage !== 'complete' ? requestedStage : 'discovery');
    let nextStep = action === 'end'
      ? 'close'
      : (NEXT_STEPS.has(parsed.next_step) && parsed.next_step !== 'close' ? parsed.next_step : 'answer_and_qualify');
    // DeepSeek owns the semantic readiness decision. Once its structured output
    // says qualification is sufficient, prevent another unnecessary interview
    // question and transition to a low-pressure call invitation.
    if (action === 'continue' && conversationStage === 'qualified_for_call' && calendarState === 'none') {
      nextStep = 'offer_call';
    }
    const result = {
      action,
      intent: String(parsed.intent || 'unclear').slice(0, 80),
      outcome,
      lead_status: leadStatus,
      confidence: Math.max(0, Math.min(1, Number(parsed.confidence) || 0)),
      conversation_stage: conversationStage,
      next_step: nextStep,
      qualification_focus: nextStep === 'offer_call' ? '' : String(parsed.qualification_focus || '').slice(0, 160),
      calendar: {
        state: calendarState,
        should_fetch_availability: ['call_accepted', 'time_proposed', 'booking_confirmed'].includes(calendarState),
        requested_time_text: String(parsed.calendar?.requested_time_text || '').slice(0, 160),
        reason: String(parsed.calendar?.reason || '').slice(0, 200)
      },
      reason: String(parsed.reason || '').slice(0, 240)
    };
    console.log(`[ai-disposition] contact=${conversation.contact} action=${result.action} intent=${result.intent} outcome=${result.outcome} stage=${result.conversation_stage} next_step=${result.next_step} calendar=${result.calendar.state} status=${result.lead_status} confidence=${result.confidence.toFixed(2)} reason=${result.reason}`);
    return result;
  } catch (error) {
    const fallback = fallbackDisposition(latestInbound, error);
    console.warn(`[ai-disposition] contact=${conversation.contact} status=fallback action=${fallback.action} intent=${fallback.intent} error=${error.message}`);
    return fallback;
  }
}

function approvedLeadDetails(conversation) {
  let lead = { available: false };
  try {
    lead = conversation.lead_context ? JSON.parse(conversation.lead_context) : lead;
  } catch {
    lead = { available: false, error: 'Stored lead context is invalid' };
  }
  const phoneSource = lead.phone_number_source || lead.contact_source || null;
  return {
    ...lead,
    phone_number_source: phoneSource || {
      available: false,
      safe_response: 'The imported lead row does not identify how the phone number was sourced. Do not claim it came from public, property, listing, county, or tax records.'
    },
    communication_capabilities: {
      send_email: false,
      retrieve_live_comps: false,
      verify_recent_sales: false,
      perform_future_follow_up: false,
      provide_callback_number: false,
      send_sms_now: true
    },
    verified_buyer: lead.verified_buyer || {
      available: false,
      safe_response: 'No specific buyer for this property is verified in the approved context. Say that plainly if asked.'
    }
  };
}

function needsBobbieKnowledge(text = '') {
  return /\b(?:Bobbie|RE\/MAX|broker|brokerage|company|office|license|experience|years|buyers?|sellers?|commission|fees?|rate|sales|listings?|process|services?)\b/i.test(text);
}

async function approvedRuntimeContext(conversation, history) {
  const latestInbound = [...history].reverse().find(item => item.direction === 'inbound')?.text || '';
  const lead = approvedLeadDetails(conversation);
  console.log(`[ai-tool] contact=${conversation.contact} tool=get_lead_details status=started mode=preload`);
  console.log(`[ai-tool] contact=${conversation.contact} tool=get_lead_details status=completed mode=preload`);
  let knowledge = { available: false, reason: 'The latest message does not require a Bobbie knowledge lookup.' };
  if (needsBobbieKnowledge(latestInbound)) {
    console.log(`[ai-tool] contact=${conversation.contact} tool=search_bobbie_knowledge status=started mode=preload`);
    try {
      knowledge = { available: true, ...(await searchBobbieKnowledge(latestInbound, 3)) };
      console.log(`[ai-tool] contact=${conversation.contact} tool=search_bobbie_knowledge status=completed mode=preload`);
    } catch (error) {
      knowledge = { available: false, error: error.message };
      console.warn(`[ai-tool] contact=${conversation.contact} tool=search_bobbie_knowledge status=failed mode=preload error=${error.message}`);
    }
  }
  return { latestInbound, lead, knowledge };
}

async function generateAiReply(conversation, history, schedulingContext = '', dispositionControl = '', fallbackPlan = {}) {
  const approved = await approvedRuntimeContext(conversation, history);
  const messages = [
    { role: 'system', content: buildBobbiePrompt(conversation, schedulingContext) },
    { role: 'system', content: `Approved runtime context (facts only): ${JSON.stringify({ lead: approved.lead, bobbie_knowledge: approved.knowledge })}` },
    ...(dispositionControl ? [{ role: 'system', content: dispositionControl }] : []),
    ...history.slice(-30).map(item => ({ role: item.direction === 'outbound' ? 'assistant' : 'user', content: item.text || '' })).filter(item => item.content)
  ];
  let content = '';
  for (let round = 0; round < 3; round += 1) {
    const assistant = await completion(messages, { tools, tool_choice: 'auto', temperature: 0.4, max_tokens: 120 });
    const calls = Array.isArray(assistant.tool_calls) ? assistant.tool_calls : [];
    if (!calls.length) { content = assistant.content || ''; break; }
    messages.push({ role: 'assistant', content: assistant.content || '', tool_calls: calls });
    for (const call of calls) {
      const name = call?.function?.name || 'unknown';
      console.log(`[ai-tool] contact=${conversation.contact} tool=${name} status=started`);
      let result;
      try {
        if (name === 'get_lead_details') result = approved.lead;
        else if (name === 'search_bobbie_knowledge') result = await searchBobbieKnowledge(String(JSON.parse(call.function.arguments || '{}').query || '').slice(0, 500), 4);
        else result = { error: 'Unknown tool' };
      } catch (error) { result = { error: error.message }; }
      messages.push({ role: 'tool', tool_call_id: call.id, content: JSON.stringify(result) });
      console.log(`[ai-tool] contact=${conversation.contact} tool=${name} status=completed`);
    }
  }
  let reply = cleanReply(content);
  if (!reply) {
    console.warn(`[ai-policy] contact=${conversation.contact} draft=empty retrying=once`);
    try {
      const retried = await completion([
        ...messages,
        { role: 'system', content: 'Return one non-empty, complete Bobbie SMS now. Use the approved context and answer the latest message in at most 220 characters.' }
      ], { temperature: 0.1, max_tokens: 80 });
      reply = cleanReply(retried.content || '');
    } catch (error) {
      console.warn(`[ai-policy] contact=${conversation.contact} empty-retry=failed error=${error.message}`);
    }
    if (!reply) reply = safeGroundedFallback(approved.latestInbound, [], history, fallbackPlan);
  }
  const phoneSourceKnown = Boolean(approved.lead?.phone_number_source?.available);
  let violations = findReplyPolicyViolations(reply, { phoneSourceKnown, maxLength: 240, history });
  if (violations.length) {
    console.warn(`[ai-policy] contact=${conversation.contact} draft=blocked violations=${violations.join(',')}`);
    const correction = `Rewrite Bobbie's answer from scratch in at most 220 characters. Fix these violations: ${violations.join(', ')}. Use only the approved runtime context, answer the latest owner question first, and output one complete SMS.`;
    let repairedReply = '';
    try {
      const repaired = await completion([...messages, { role: 'system', content: correction }], { temperature: 0.1, max_tokens: 80 });
      repairedReply = cleanReply(repaired.content || '');
    } catch (error) {
      console.warn(`[ai-policy] contact=${conversation.contact} repair=failed error=${error.message}`);
    }
    const repairedViolations = findReplyPolicyViolations(repairedReply, { phoneSourceKnown, maxLength: 240, history });
    if (repairedReply && !repairedViolations.length) {
      reply = repairedReply;
      violations = [];
      console.log(`[ai-policy] contact=${conversation.contact} draft=repaired`);
    } else {
      violations = repairedViolations.length ? repairedViolations : violations;
      reply = safeGroundedFallback(approved.latestInbound, violations, history, fallbackPlan);
      console.warn(`[ai-policy] contact=${conversation.contact} fallback=grounded violations=${violations.join(',')}`);
    }
  }
  return fitCompleteSms(reply, 240);
}

function normalizedScheduleText(value = '') {
  return String(value)
    .toLowerCase()
    .replace(/\b0+(\d)\b/g, '$1')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function slotLabelParts(label = '') {
  const match = String(label).match(/^([^,]+),\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s+at\s+(\d{1,2}):([0-5]\d)\s+(AM|PM)/i);
  if (!match) return null;
  return {
    weekday: match[1].toLowerCase(),
    month: match[2].toLowerCase(),
    day: String(Number(match[3])),
    hour: String(Number(match[5])),
    minute: match[6],
    period: match[7].toLowerCase()
  };
}

function exactOfferedSlot(history, availability) {
  const latestInboundIndex = history.findLastIndex(item => item.direction === 'inbound');
  if (latestInboundIndex < 1 || history[latestInboundIndex - 1]?.direction !== 'outbound') return null;
  const ownerText = normalizedScheduleText(history[latestInboundIndex].text || '');
  const bobbieText = normalizedScheduleText(history[latestInboundIndex - 1].text || '');
  const ownerTime = ownerText.match(/\b(1[0-2]|[1-9])(?:\s+([0-5]\d))?\s+(am|pm)\b/);
  if (!ownerTime) return null;
  const ownerClock = `${Number(ownerTime[1])}:${ownerTime[2] || '00'}:${ownerTime[3]}`;
  const offered = (availability.slots || []).filter(slot => {
    const parts = slotLabelParts(slot.label);
    if (!parts) return false;
    const fullLabel = normalizedScheduleText(`${parts.weekday} ${parts.month} ${parts.day} at ${parts.hour} ${parts.minute} ${parts.period}`);
    const dateAndTime = normalizedScheduleText(`${parts.month} ${parts.day} at ${parts.hour} ${parts.minute} ${parts.period}`);
    return bobbieText.includes(fullLabel) || bobbieText.includes(dateAndTime);
  });
  const timeMatches = offered.filter(slot => {
    const parts = slotLabelParts(slot.label);
    return parts && `${parts.hour}:${parts.minute}:${parts.period}` === ownerClock;
  });
  if (!timeMatches.length) return null;
  const datedMatches = timeMatches.filter(slot => {
    const parts = slotLabelParts(slot.label);
    return ownerText.includes(`${parts.month} ${parts.day}`) || ownerText.includes(parts.weekday);
  });
  const matches = datedMatches.length ? datedMatches : timeMatches;
  return matches.length === 1 ? matches[0] : null;
}

async function resolveCalendarAction(conversation, history, availability, calendarDecision = {}) {
  console.log(`[calendar-tool] DeepSeek calendar resolution started for ${conversation.contact} state=${calendarDecision.state || 'none'}`);
  const slots = availability.slots || [];
  const recoveredSlot = exactOfferedSlot(history, availability);
  if (calendarDecision.state === 'booking_confirmed' && recoveredSlot) {
    console.log(`[calendar-tool] exact offered-slot selection resolved locally for ${conversation.contact}: ${recoveredSlot.start_at}`);
    return { action: 'book', consent: 'offered_slot_selected', ...recoveredSlot, reason: 'Exact selection matched Bobbie\'s immediately preceding live-calendar options' };
  }
  const system = `Resolve the owner's scheduling turn against live availability. Disposition calendar state: ${calendarDecision.state || 'none'}; requested words: ${calendarDecision.requested_time_text || ''}; current time: ${availability.current_time}; timezone: ${availability.timezone}.
Available slots:
${slots.map(slot => `${slot.start_at}|${slot.end_at}|${slot.label}`).join('\n')}
Return only JSON: {"action":"none|offer_alternatives|ask_confirmation|book","consent":"none|owner_proposed|offered_slot_selected|confirmed_exact","start_at":null,"end_at":null,"reason":"brief"}.
Independently compare Bobbie's immediately preceding message with the owner's latest reply. Use offered_slot_selected only when Bobbie offered that exact slot as a live option and the owner selected it. Use owner_proposed when the owner introduced a day/time Bobbie had not offered. Use confirmed_exact when Bobbie asked to book one exact verified slot and the owner agreed.
For time_proposed: choose ask_confirmation only when one exact owner-proposed slot is available; otherwise offer_alternatives. Never book an owner-proposed time until Bobbie has verified it and asked for confirmation.
For booking_confirmed: choose book when the owner either selected one exact slot from Bobbie's immediately preceding available options or confirmed Bobbie's request to book one exact verified slot. Selecting an offered option is already confirmation; do not ask again. Copy timestamps exactly. Otherwise choose none or offer_alternatives.
For call_accepted choose none because the server will offer live slots. Never infer consent from an older question.`;
  const baseMessages = [
    { role: 'system', content: system },
    ...history.slice(-14).map(item => ({ role: item.direction === 'outbound' ? 'assistant' : 'user', content: item.text || '' }))
  ];
  let lastError = null;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const messages = attempt === 1
        ? baseMessages
        : [...baseMessages, { role: 'system', content: 'Your previous response was empty or invalid. Return the requested JSON object now, with no prose or markdown.' }];
      const assistant = await completion(messages, {
        temperature: 0,
        max_tokens: 180,
        ...(attempt === 1 ? { response_format: { type: 'json_object' } } : {})
      });
      const match = String(assistant.content || '').match(/\{[\s\S]*\}/);
      if (!match) throw new Error('Calendar resolver returned no JSON object');
      const decision = JSON.parse(match[0]);
      const slot = slots.find(item => item.start_at === decision.start_at && item.end_at === decision.end_at);
      const requestedAction = ['none', 'offer_alternatives', 'ask_confirmation', 'book'].includes(decision.action) ? decision.action : 'none';
      const consent = ['none', 'owner_proposed', 'offered_slot_selected', 'confirmed_exact'].includes(decision.consent)
        ? decision.consent
        : 'none';
      const bookingConsent = calendarDecision.state === 'booking_confirmed'
        || consent === 'offered_slot_selected'
        || consent === 'confirmed_exact';
      const action = requestedAction === 'book' && !bookingConsent
        ? 'ask_confirmation'
        : requestedAction === 'ask_confirmation' && bookingConsent && slot
          ? 'book'
          : requestedAction;
      if (['ask_confirmation', 'book'].includes(action) && !slot) {
        return { action: 'offer_alternatives', consent, start_at: null, end_at: null, reason: 'Requested slot is not currently available' };
      }
      return { action, consent, ...(slot || { start_at: null, end_at: null }), reason: String(decision.reason || '').slice(0, 200) };
    } catch (error) {
      lastError = error;
      console.warn(`[calendar-tool] DeepSeek calendar resolution attempt ${attempt} failed for ${conversation.contact}: ${error.message}`);
    }
  }
  if (recoveredSlot) {
    console.log(`[calendar-tool] resolver fallback matched exact offered slot for ${conversation.contact}: ${recoveredSlot.start_at}`);
    return { action: 'book', consent: 'offered_slot_selected', ...recoveredSlot, reason: 'DeepSeek resolver unavailable; exact selection safely matched a live offered slot' };
  }
  return { action: 'resolution_failed', consent: 'none', start_at: null, end_at: null, reason: `Calendar interpretation unavailable: ${lastError?.message || 'unknown error'}` };
}

async function reviewAiReply(conversation, history, draft, disposition, schedulingContext = '') {
  const recent = history.slice(-16).map(item => ({ role: item.direction === 'outbound' ? 'Bobbie' : 'Owner', text: String(item.text || '') }));
  const system = `You are an independent semantic QA reviewer for Bobbie's property-owner SMS. Determine whether the draft is the response the owner would reasonably expect next.
Return only JSON: {"valid":true,"issues":["short_issue"],"rewrite_instruction":"specific instruction or empty","reason":"brief"}.
A valid reply must answer the owner's latest message as a whole, follow the supplied disposition and required next step, make sense after the immediately preceding turn, avoid repeating answered questions, avoid unsupported claims, and avoid jumping to a call or calendar unless the supplied calendar state permits it. When next_step is offer_call, another qualification question without a low-pressure call invitation is invalid. Any promise to book or confirm later is invalid: a booking claim may only come from the calendar API after creation succeeds. Selecting one of Bobbie's immediately preceding live slots is already booking consent and should produce the real calendar confirmation, not a conversational placeholder. A bare "yes" answers the immediately preceding question, not an older invitation. Be strict about non sequiturs, over-qualification, and premature scheduling, but do not reject concise natural SMS merely for style.`;
  try {
    const assistant = await completion([
      { role: 'system', content: system },
      { role: 'user', content: JSON.stringify({ disposition, scheduling_context: schedulingContext, recent_conversation: recent, proposed_bobbie_reply: draft }) }
    ], { temperature: 0, max_tokens: 160 });
    const match = String(assistant.content || '').match(/\{[\s\S]*\}/);
    if (!match) throw new Error('Reviewer returned no JSON object');
    const parsed = JSON.parse(match[0]);
    const result = {
      valid: parsed.valid === true,
      issues: Array.isArray(parsed.issues) ? parsed.issues.map(issue => String(issue).slice(0, 100)).slice(0, 6) : [],
      rewrite_instruction: String(parsed.rewrite_instruction || '').slice(0, 400),
      reason: String(parsed.reason || '').slice(0, 240)
    };
    console.log(`[ai-review] contact=${conversation.contact} valid=${result.valid} issues=${result.issues.join(',') || 'none'} reason=${result.reason}`);
    return result;
  } catch (error) {
    console.warn(`[ai-review] contact=${conversation.contact} status=unavailable action=keep_policy_checked_draft error=${error.message}`);
    return { valid: true, issues: [], rewrite_instruction: '', reason: 'Reviewer unavailable; policy-checked draft retained' };
  }
}

async function reviewAndRepairAiReply(conversation, history, draft, disposition, schedulingContext = '', dispositionControl = '') {
  const review = await reviewAiReply(conversation, history, draft, disposition, schedulingContext);
  if (review.valid) return draft;
  const repairControl = `${dispositionControl}\nIndependent reviewer rejected the previous draft. Issues: ${review.issues.join(', ') || 'semantic mismatch'}. Rewrite instruction: ${review.rewrite_instruction || review.reason}. Write a fresh response that fixes these issues; never defend or mention the previous draft.`;
  const repaired = await generateAiReply(conversation, history, schedulingContext, repairControl, { next_step: disposition.next_step });
  console.log(`[ai-review] contact=${conversation.contact} action=rewritten`);
  return repaired;
}

export { analyzeConversationDisposition, approvedLeadDetails, exactOfferedSlot, generateAiReply, needsBobbieKnowledge, resolveCalendarAction, reviewAiReply, reviewAndRepairAiReply };
