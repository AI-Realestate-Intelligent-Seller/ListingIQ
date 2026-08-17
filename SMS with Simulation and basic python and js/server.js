import http from 'http';
import fs from 'fs';
import path from 'path';
import { randomUUID } from 'crypto';
import { fileURLToPath } from 'url';
import { getOrCreateConversation, getConversation, addMessage, listConversations, getMessages, updateConversationSettings, findMessageByTelnyxId, updateMessageStatusByTelnyxId, deleteMessage, deleteConversation, updateLeadProgress } from './db.js';
import { analyzeConversationDisposition, generateAiReply, resolveCalendarAction, reviewAndRepairAiReply } from './ai-runtime.js';
import { enqueueLead, startLeadWorker, queueCounts } from './lead-queue.js';
import { containsSchedulingPressure, containsTimeProposal, containsUnbookedConfirmation } from './scheduling-state.js';
import { classifyLeadMessage, finalizedLeadStatus, isOptOut, mergeLeadStatus } from './lead-classifier.js';
import { buildInitialOutreach, buildSingleLeadContext } from './outreach.js';

const PORT = Number(process.env.PORT) || 5000;
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Node does not load .env automatically when started as `node server.js`.
// Use Node's built-in loader when available; exported shell variables still
// take precedence over values in the file.
const envFile = path.join(__dirname, '.env');
if (typeof process.loadEnvFile === 'function' && fs.existsSync(envFile)) {
  process.loadEnvFile(envFile);
}

const clients = new Set();
const aiReplyTimers = new Map();
const FIXED_FROM = '+12245798015';
const SMS_MODE = (process.env.SMS_MODE || 'simulation').toLowerCase();
const SIMULATION_SMS_URL = process.env.SIMULATION_SMS_URL || 'http://127.0.0.1:5051/v2/messages';
const TELNYX_SMS_URL = 'https://api.telnyx.com/v2/messages';
const CALENDAR_API_URL = (process.env.CALENDAR_API_URL || 'http://127.0.0.1:5052').replace(/\/$/, '');

function sendResponse(res, statusCode, body, contentType = 'text/plain') {
  res.writeHead(statusCode, { 'Content-Type': contentType });
  res.end(body);
}

function sendFile(res, filePath, contentType) {
  const fullPath = path.join(__dirname, filePath);
  fs.readFile(fullPath, (err, data) => {
    if (err) {
      sendResponse(res, 404, 'Not found');
      return;
    }
    sendResponse(res, 200, data, contentType);
  });
}

function broadcastEvent(event, data) {
  const body = `event: ${event}\n` + `data: ${JSON.stringify(data)}\n\n`;
  for (const client of clients) {
    client.write(body);
  }
}

function parseJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => {
      body += chunk.toString();
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(body || '{}'));
      } catch (error) {
        reject(error);
      }
    });
    req.on('error', reject);
  });
}

async function sendTelnyxMessage(data) {
  if (!['simulation', 'telnyx'].includes(SMS_MODE)) {
    throw new Error("SMS_MODE must be either 'simulation' or 'telnyx'");
  }

  // Always send from the fixed sending number to prevent accidental overrides
  const bodyData = Object.assign({}, data, { from: FIXED_FROM });
  const isSimulation = SMS_MODE === 'simulation';
  const messageUrl = isSimulation ? SIMULATION_SMS_URL : TELNYX_SMS_URL;
  const headers = { 'Content-Type': 'application/json' };

  if (!isSimulation) {
    delete bodyData.simulation_event_type;
    delete bodyData.simulation_recipient_enabled;
    delete bodyData.simulation_lead_context;
    delete bodyData.suppress_auto_reply;
    const apiKey = process.env.TELNYX_API_KEY;
    if (!apiKey) {
      throw new Error('TELNYX_API_KEY environment variable is required when SMS_MODE=telnyx');
    }
    headers.Authorization = `Bearer ${apiKey}`;
  }

  let response;
  try {
    response = await fetch(messageUrl, {
      method: 'POST',
      headers,
      body: JSON.stringify(bodyData)
    });
  } catch (error) {
    if (isSimulation) {
      throw new Error(`Outbound simulator is unavailable at ${messageUrl}. Start it with: python3 Simulation/outbound.py`);
    }
    throw error;
  }

  const responseBody = await response.text();
  let json;
  try {
    json = JSON.parse(responseBody);
  } catch {
    throw new Error(`${isSimulation ? 'Outbound simulator' : 'Telnyx'} responded with non-JSON: ${responseBody}`);
  }

  if (!response.ok) {
    const errorMessage = json?.errors?.[0]?.detail || JSON.stringify(json);
    throw new Error(`${isSimulation ? 'Outbound simulator' : 'Telnyx'} error: ${errorMessage}`);
  }

  return json;
}

async function sendAndStoreMessage(contact, text, eventType = 'message.sent', suppressAutoReply = false) {
  const conversation = getOrCreateConversation(contact);
  const telnyxResponse = await sendTelnyxMessage({
    to: contact,
    text,
    simulation_event_type: eventType,
    simulation_recipient_enabled: Boolean(conversation.recipient_ai_enabled),
    simulation_lead_context: conversation.lead_context ? JSON.parse(conversation.lead_context) : null,
    suppress_auto_reply: suppressAutoReply
  });
  const messageId = addMessage(conversation.id, {
    direction: 'outbound',
    from_number: FIXED_FROM,
    to_number: contact,
    text,
    status: 'queued',
    event_type: eventType,
    telnyx_id: telnyxResponse?.data?.id || null
  });
  broadcastEvent('message.updated', { contact, message_id: messageId });
  return { telnyxResponse, messageId };
}

function isConversationClosing(text = '') {
  const normalized = String(text).trim().replace(/^[^\p{L}\p{N}]+/u, '');
  if (!normalized || normalized.includes('?')) return false;
  return (
    /(?:^|[,.!]\s*)(?:bye(?:\s+for\s+now)?|good\s*bye|take\s+care|have\s+a\s+(?:(?:good|great|nice)\s+(?:day|evening|night|weekend|one)|good\s+rest\s+of\s+(?:your|the)\s+day)|you\s+too(?:,?\s*(?:bye|good\s*bye))?|talk\s+(?:soon|then|tomorrow)(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?|see\s+you\s+(?:soon|then|tomorrow(?:\s+at\s+[\w:]+(?:\s*[ap]m)?)?|at\s+[\w:]+(?:\s*[ap]m)?))\s*[,!.]*(?:\s+[A-Za-z][A-Za-z'’-]*)?[!.]*$/i.test(normalized)
    || /^(?:thanks?|thank\s+you),?\s+(?:you\s+(?:too|as\s+well)|same\s+to\s+you)\s*[!.]*$/i.test(normalized)
    || /^(?:sounds\s+good|looking\s+forward\s+to\s+it)[,!.].*\b(?:see\s+you|talk)\s+(?:soon|then|tomorrow|at\s+[\w:]+(?:\s*[ap]m)?)\s*[!.]*$/i.test(normalized)
  );
}

function recordInboundClassification(contact, text) {
  const classification = classifyLeadMessage(text);
  if (!classification) {
    console.log(`[lead-classifier] contact=${contact} status=unchanged terminal=false`);
    return null;
  }
  const conversation = getConversation(contact);
  const leadStatus = mergeLeadStatus(conversation?.lead_status, classification.lead_status);
  updateLeadProgress(contact, {
    lead_status: leadStatus,
    ...(classification.dnc_alert !== undefined ? { dnc_alert: classification.dnc_alert } : {})
  });
  console.log(`[lead-classifier] contact=${contact} status=${leadStatus} terminal=${Boolean(classification.terminal)} dnc=${Boolean(classification.dnc_alert)}`);
  return { ...classification, lead_status: leadStatus };
}

function markLeadCompleted(contact, values = {}) {
  const conversation = getConversation(contact);
  if (!conversation) return;
  updateLeadProgress(contact, {
    ...values,
    queue_status: 'completed',
    lead_status: values.lead_status || finalizedLeadStatus(conversation.lead_status),
    processed_at: values.processed_at || new Date().toISOString()
  });
}

function safeAvailableOffer(availability) {
  const ordered = availability.slots || [];
  const slots = [];
  const days = new Set();
  for (const slot of ordered) {
    const day = String(slot.label || '').split(' at ')[0];
    if (days.has(day)) continue;
    days.add(day);
    slots.push(slot);
    if (slots.length === 2) break;
  }
  if (!slots.length) return 'I don’t have an open time to confirm yet. What other day works for a quick call?';
  const labels = slots.map(slot => slot.label.replace(/, 20\d{2}/, '').replace(/ America\/.+$/, ''));
  return labels.length === 1
    ? `I can do ${labels[0]}. Does that work?`
    : `I can do ${labels[0]} or ${labels[1]}. Which works?`;
}

async function fetchCalendarAvailability() {
  console.log(`[calendar-tool] availability request -> ${CALENDAR_API_URL}`);
  const response = await fetch(`${CALENDAR_API_URL}/api/availability`, {
    signal: AbortSignal.timeout(7000)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Calendar returned HTTP ${response.status}`);
  console.log(`[calendar-tool] availability received: ${(payload.slots || []).length} open slots`);
  return payload;
}

function calendarPromptContext(availability, note = '') {
  const slots = (availability?.slots || []).map(slot => `- ${slot.label}`).join('\n');
  return `Current time: ${availability.current_time}\nTimezone: ${availability.timezone}\nEach meeting is ${availability.slot_minutes} minutes.\n${note ? `${note}\n` : ''}Offer only these exact available starts; never invent availability:\n${slots}`;
}

async function createCalendarBooking(conversation, slot) {
  console.log(`[calendar-tool] booking request for ${conversation.contact}: ${slot.start_at}`);
  const response = await fetch(`${CALENDAR_API_URL}/api/bookings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      phone: conversation.contact,
      name: conversation.name || '',
      title: 'Property consultation with Bobbie',
      start_at: slot.start_at,
      end_at: slot.end_at
    }),
    signal: AbortSignal.timeout(15000)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error || `Calendar returned HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  console.log(`[calendar-tool] booking created for ${conversation.contact}: ${payload.start_at}`);
  return payload;
}

async function processAiReply(contact, latestInboundText) {
  const conversation = getConversation(contact);
  if (!conversation?.ai_enabled) return;
  try {
    if (isOptOut(latestInboundText)) {
      const reply = 'Understood — I’ll remove you from my outreach list. Take care.';
      updateConversationSettings(contact, { ai_enabled: false });
      markLeadCompleted(contact, { lead_status: 'dnc', dnc_alert: true });
      await sendAndStoreMessage(contact, reply, 'ai.reply');
      console.log(`AI reply sent to ${contact}`);
      return;
    }

    const history = getMessages(contact);
    const disposition = await analyzeConversationDisposition(conversation, history);
    if (disposition.action === 'end') {
      const closingControl = `Final disposition: END. Intent: ${disposition.intent}. Reason: ${disposition.reason}. Write one brief, respectful closing acknowledgment suited to the owner’s latest message. Do not ask any question, offer another service, request a call, mention scheduling, or try to continue.`;
      let closingReply = await generateAiReply(conversation, history, 'Do not schedule; this conversation is ending.', closingControl);
      closingReply = await reviewAndRepairAiReply(conversation, history, closingReply, disposition, 'Do not schedule; this conversation is ending.', closingControl);
      if (!closingReply || closingReply.includes('?') || containsSchedulingPressure(closingReply)) {
        console.warn(`[ai-disposition] contact=${contact} invalid_closing=true fallback=brief_close`);
        closingReply = 'Understood. Thanks for letting me know, and take care.';
      }
      await sendAndStoreMessage(contact, closingReply, 'ai.closing');
      updateConversationSettings(contact, { ai_enabled: false });
      markLeadCompleted(contact, { lead_status: disposition.lead_status });
      broadcastEvent('conversation.updated', { contact, ai_enabled: false });
      console.log(`[ai-disposition] contact=${contact} conversation=closed intent=${disposition.intent}`);
      return;
    }

    const latestConversation = getConversation(contact);
    const continuedStatus = ['not_interested', 'no_response'].includes(latestConversation?.lead_status)
      ? disposition.lead_status
      : mergeLeadStatus(latestConversation?.lead_status, disposition.lead_status);
    updateLeadProgress(contact, { lead_status: continuedStatus, processed_at: null });
    const dispositionControl = `Final disposition: CONTINUE. Intent: ${disposition.intent}. Conversation stage: ${disposition.conversation_stage}. Required next step: ${disposition.next_step}. Qualification focus: ${disposition.qualification_focus || 'none supplied'}. Calendar state: ${disposition.calendar.state}. Reason: ${disposition.reason}. Follow the required next step naturally without assuming facts. If the next step is answer_and_qualify, answer the concern and ask one concise logical question; do not request a call yet. If it is answer_only, do not ask a question. If it is offer_call, do not qualify further: acknowledge what is already known and make one low-pressure 5-10 minute call request without proposing a time. Do not introduce calendar times unless calendar state explicitly permits it.`;
    const calendarState = disposition.calendar;
    console.log(`[calendar-state] ${contact}: ${calendarState.state} source=deepseek reason=${calendarState.reason}`);
    let schedulingContext = '';
    let schedulingFallbackReply = '';
    let lastAvailability = null;
    if (calendarState.should_fetch_availability) {
      try {
        const availability = await fetchCalendarAvailability();
        lastAvailability = availability;
        schedulingContext = `${calendarPromptContext(availability)}\nCalendar state from DeepSeek: ${calendarState.state}. No booking exists unless the booking API succeeds.`;
        if (calendarState.state === 'call_accepted') {
          schedulingFallbackReply = safeAvailableOffer(availability);
          console.log(`[calendar-tool] DeepSeek confirmed call consent; offering live slots to ${contact}: ${schedulingFallbackReply}`);
        } else {
          const calendarAction = await resolveCalendarAction(conversation, history, availability, calendarState);
          console.log(`[calendar-tool] resolved action for ${contact}: ${JSON.stringify(calendarAction)}`);
          const calendarConsentConfirmed = calendarState.state === 'booking_confirmed'
            || calendarAction.consent === 'offered_slot_selected'
            || calendarAction.consent === 'confirmed_exact';
          if (calendarAction.action === 'book' && calendarConsentConfirmed) {
            try {
              const booking = await createCalendarBooking(conversation, calendarAction);
              if (booking.confirmation_sent !== true && booking.message) {
                console.warn(`[calendar-tool] calendar could not deliver confirmation; Node fallback for ${contact}`);
                await sendAndStoreMessage(contact, booking.message, 'calendar.confirmation.fallback', true);
              }
              updateConversationSettings(contact, { ai_enabled: false });
              markLeadCompleted(contact, { lead_status: 'ready_to_sell', meeting_booked: true });
              broadcastEvent('conversation.updated', { contact, ai_enabled: false });
              console.log(`Meeting booked for ${contact}: ${booking.start_at}`);
              return;
            } catch (error) {
              if (error.status !== 409) throw error;
              const refreshed = await fetchCalendarAvailability();
              schedulingContext = calendarPromptContext(
                refreshed,
                'The requested slot was just taken. Apologize briefly and offer one or two nearby available alternatives.'
              );
              schedulingFallbackReply = `That time was just taken. ${safeAvailableOffer(refreshed)}`;
              console.log(`[calendar-tool] collision alternatives for ${contact}: ${schedulingFallbackReply}`);
            }
          } else if (calendarAction.action === 'ask_confirmation') {
            const label = String(calendarAction.label || '').replace(/, 20\d{2}/, '').replace(/ America\/.+$/, '');
            schedulingFallbackReply = `${label} is open. Should I book it for our quick call?`;
            console.log(`[calendar-tool] asking final booking confirmation for ${contact}: ${schedulingFallbackReply}`);
          } else if (calendarAction.action === 'offer_alternatives') {
            schedulingFallbackReply = `That time isn’t open. ${safeAvailableOffer(availability)}`;
            console.log(`[calendar-tool] requested time unavailable for ${contact}: ${schedulingFallbackReply}`);
          } else if (calendarAction.action === 'resolution_failed') {
            schedulingFallbackReply = 'I couldn’t verify that calendar selection just now. Please resend the exact day and time you chose.';
            console.warn(`[calendar-tool] resolver unavailable for ${contact}; no availability claim made`);
          }
        }
      } catch (error) {
        console.error(`Calendar scheduling unavailable for ${contact}:`, error.message);
        schedulingContext = 'The live calendar is currently unavailable. Do not claim availability or a booking, and do not invent or repeat a time.';
      }
    } else if (calendarState.state === 'call_declined') {
      schedulingContext = 'The recipient declined or deferred a call. Answer their latest question directly. Do not offer times, ask for a call, or pressure them to schedule.';
    }

    const fallbackPlan = { next_step: disposition.next_step };
    let reply = schedulingFallbackReply || await generateAiReply(conversation, history, schedulingContext, dispositionControl, fallbackPlan);
    const unbookedConfirmation = containsUnbookedConfirmation(reply);
    const unverifiedTimeProposal = !lastAvailability && containsTimeProposal(reply);
    if (unbookedConfirmation || unverifiedTimeProposal) {
      console.warn(`[calendar-tool] blocked unbooked confirmation for ${contact}: ${reply}`);
      reply = lastAvailability && calendarState.should_fetch_availability
        ? safeAvailableOffer(lastAvailability)
        : calendarState.state === 'call_accepted'
          ? 'The live calendar is unavailable, so I can’t offer a verified time right now.'
          : 'I haven’t scheduled anything, and I won’t invent a time.';
    }
    if (calendarState.state === 'call_declined' && containsSchedulingPressure(reply)) {
      console.warn(`[calendar-state] blocked scheduling pressure after refusal for ${contact}: ${reply}`);
      const retryContext = `${schedulingContext}\nThe previous draft improperly reintroduced a call. Reply directly by text without mentioning a call, meeting, schedule, appointment, email, or future follow-up.`;
      const retry = await generateAiReply(conversation, history, retryContext, dispositionControl, fallbackPlan);
      reply = containsSchedulingPressure(retry)
        ? 'Understood—I’ll keep this to text and answer what I can here.'
        : retry;
    }
    reply = await reviewAndRepairAiReply(conversation, history, reply, disposition, schedulingContext, dispositionControl);
    if (containsUnbookedConfirmation(reply) || (!lastAvailability && containsTimeProposal(reply))) {
      console.warn(`[calendar-tool] reviewer rewrite violated calendar safety for ${contact}: ${reply}`);
      reply = lastAvailability && calendarState.should_fetch_availability
        ? safeAvailableOffer(lastAvailability)
        : 'I can’t verify a calendar time right now, so I won’t suggest or confirm one.';
    }
    if (calendarState.state === 'call_declined' && containsSchedulingPressure(reply)) {
      console.warn(`[calendar-state] reviewer rewrite reintroduced scheduling after refusal for ${contact}`);
      reply = 'Understood—I’ll keep this to text and answer what I can here.';
    }
    await sendAndStoreMessage(contact, reply, 'ai.reply');
    if (isConversationClosing(reply)) {
      updateConversationSettings(contact, { ai_enabled: false });
      markLeadCompleted(contact);
      broadcastEvent('conversation.updated', { contact, ai_enabled: false });
      console.log(`Bobbie closed the conversation with ${contact}; AI autopilot stopped`);
    }
    console.log(`AI reply sent to ${contact}`);
  } catch (error) {
    console.error(`AI reply failed for ${contact}:`, error.message);
    broadcastEvent('ai.error', { contact, error: error.message });
  }
}

function scheduleAiReply(contact, latestInboundText) {
  clearTimeout(aiReplyTimers.get(contact));
  const timer = setTimeout(async () => {
    aiReplyTimers.delete(contact);
    await processAiReply(contact, latestInboundText);
  }, 1400);
  aiReplyTimers.set(contact, timer);
}

async function processQueuedLead(job) {
  const { contact, text } = job.data;
  const cooldownMs = Number(process.env.LEAD_QUEUE_COOLDOWN_MS || 5000);
  const finishWithCooldown = async () => {
    if (cooldownMs <= 0) return;
    console.log(`[lead-queue] ${contact} finished; waiting ${cooldownMs}ms before the next lead`);
    await new Promise(resolve => setTimeout(resolve, cooldownMs));
  };
  updateConversationSettings(contact, { ai_enabled: true, recipient_ai_enabled: true });
  updateLeadProgress(contact, { queue_status: 'active', lead_status: 'processing' });
  await sendAndStoreMessage(contact, text, 'outreach.bulk-simulation');
  const timeoutMs = Number(process.env.LEAD_JOB_TIMEOUT_MS || 0);
  const deadline = timeoutMs > 0 ? Date.now() + timeoutMs : null;
  while (!deadline || Date.now() < deadline) {
    const conversation = getConversation(contact);
    if (!conversation || conversation.processed_at || !conversation.ai_enabled) {
      if (conversation && !conversation.processed_at) markLeadCompleted(contact);
      else if (conversation) updateLeadProgress(contact, { queue_status: 'completed' });
      await finishWithCooldown();
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1500));
  }
  updateConversationSettings(contact, { ai_enabled: false });
  markLeadCompleted(contact, { lead_status: 'no_response' });
  console.warn(`[lead-queue] timed out ${contact} after ${timeoutMs}ms; moved to next lead`);
  await finishWithCooldown();
}

startLeadWorker(processQueuedLead);

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', `http://localhost:${PORT}`);

  if (req.method === 'GET' && url.pathname === '/') {
    sendFile(res, 'index.html', 'text/html');
    return;
  }

  // Conversations API
  if (req.method === 'GET' && url.pathname === '/conversations') {
    try {
      const rows = listConversations().map(row => ({ ...row, simulation_mode: SMS_MODE === 'simulation' }));
      sendResponse(res, 200, JSON.stringify(rows), 'application/json');
    } catch (err) {
      sendResponse(res, 500, JSON.stringify({ error: err.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'GET' && url.pathname.startsWith('/conversations/')) {
    try {
      const contact = decodeURIComponent(url.pathname.replace('/conversations/', ''));
      const messages = getMessages(contact);
      sendResponse(res, 200, JSON.stringify(messages), 'application/json');
    } catch (err) {
      sendResponse(res, 500, JSON.stringify({ error: err.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/conversations') {
    try {
      const body = await parseJsonBody(req);
      if (!body.contact) {
        sendResponse(res, 400, JSON.stringify({ error: 'contact required' }), 'application/json');
        return;
      }
      getOrCreateConversation(body.contact, body.name || null);
      updateConversationSettings(body.contact, {
        ...(body.name !== undefined ? { name: body.name } : {}),
        ...(body.property_address !== undefined ? { property_address: body.property_address } : {}),
        ...(body.ai_enabled !== undefined ? { ai_enabled: body.ai_enabled } : {}),
        ...(body.recipient_ai_enabled !== undefined ? { recipient_ai_enabled: body.recipient_ai_enabled } : {})
      });
      sendResponse(res, 200, JSON.stringify({ ok: true }), 'application/json');
    } catch (err) {
      sendResponse(res, 500, JSON.stringify({ error: err.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/outreach') {
    try {
      const body = await parseJsonBody(req);
      const contact = String(body.contact || '').trim();
      const propertyAddress = String(body.property_address || '').trim();
      const name = String(body.name || '').trim();
      const outreachReason = String(body.outreach_reason || '').trim();
      const aiEnabled = body.ai_enabled !== false;
      const recipientAiEnabled = body.recipient_ai_enabled !== false;
      if (!/^\+[1-9]\d{6,14}$/.test(contact)) {
        sendResponse(res, 400, JSON.stringify({ error: 'A valid E.164 contact number is required' }), 'application/json');
        return;
      }
      if (!propertyAddress) {
        sendResponse(res, 400, JSON.stringify({ error: 'Property address is required for AI outreach' }), 'application/json');
        return;
      }
      if (!name) {
        sendResponse(res, 400, JSON.stringify({ error: 'Contact name is required' }), 'application/json');
        return;
      }
      if (!outreachReason) {
        sendResponse(res, 400, JSON.stringify({ error: 'Outreach reason is required' }), 'application/json');
        return;
      }
      if (name.length > 120 || propertyAddress.length > 300 || outreachReason.length > 300) {
        sendResponse(res, 400, JSON.stringify({ error: 'One or more conversation fields are too long' }), 'application/json');
        return;
      }
      const existing = getConversation(contact);
      if (existing && getMessages(contact).length) {
        sendResponse(res, 409, JSON.stringify({ error: 'This contact already has a conversation. Open it instead of sending another introduction.' }), 'application/json');
        return;
      }
      getOrCreateConversation(contact, name);
      updateConversationSettings(contact, {
        name,
        property_address: propertyAddress,
        ai_enabled: aiEnabled,
        recipient_ai_enabled: recipientAiEnabled,
        lead_context: JSON.stringify(buildSingleLeadContext(propertyAddress, outreachReason))
      });
      const text = buildInitialOutreach(name, propertyAddress, outreachReason);
      if (!aiEnabled) {
        updateLeadProgress(contact, { queue_status: 'idle', lead_status: 'processing', processed_at: null });
        sendResponse(res, 200, JSON.stringify({ ok: true, started: false, queued: false, simulation: SMS_MODE === 'simulation' }), 'application/json');
        return;
      }
      if (SMS_MODE === 'simulation' && recipientAiEnabled) {
        updateConversationSettings(contact, { ai_enabled: false });
        updateLeadProgress(contact, { queue_status: 'waiting', lead_status: 'processing', dnc_alert: false, meeting_booked: false, processed_at: null });
        try {
          const job = await enqueueLead({ contact, text });
          sendResponse(res, 200, JSON.stringify({ ok: true, started: true, queued: true, job_id: job.id, text, simulation: true }), 'application/json');
        } catch (error) {
          updateLeadProgress(contact, { queue_status: 'failed' });
          throw error;
        }
        return;
      }
      const result = await sendAndStoreMessage(contact, text, 'outreach.initial');
      sendResponse(res, 200, JSON.stringify({
        ok: true,
        started: true,
        queued: false,
        message_id: result.messageId,
        text,
        simulation: SMS_MODE === 'simulation'
      }), 'application/json');
    } catch (error) {
      sendResponse(res, 500, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'DELETE' && url.pathname.startsWith('/conversations/')) {
    try {
      const contact = decodeURIComponent(url.pathname.replace('/conversations/', ''));
      const deleted = deleteConversation(contact);
      if (!deleted) {
        sendResponse(res, 404, JSON.stringify({ error: 'Conversation not found' }), 'application/json');
        return;
      }
      sendResponse(res, 200, JSON.stringify({ ok: true }), 'application/json');
    } catch (err) {
      sendResponse(res, 500, JSON.stringify({ error: err.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'DELETE' && /^\/messages\/\d+$/.test(url.pathname)) {
    try {
      const id = Number(url.pathname.split('/').pop());
      const deleted = deleteMessage(id);
      if (!deleted) {
        sendResponse(res, 404, JSON.stringify({ error: 'Message not found' }), 'application/json');
        return;
      }
      sendResponse(res, 200, JSON.stringify({ ok: true }), 'application/json');
    } catch (err) {
      sendResponse(res, 500, JSON.stringify({ error: err.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'GET' && url.pathname === '/client.js') {
    sendFile(res, 'client.js', 'application/javascript');
    return;
  }

  if (req.method === 'GET' && url.pathname === '/styles.css') {
    sendFile(res, 'styles.css', 'text/css');
    return;
  }

  if (req.method === 'GET' && url.pathname === '/events') {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive'
    });
    res.write('\n');
    clients.add(res);

    req.on('close', () => {
      clients.delete(res);
    });
    return;
  }

  if (req.method === 'POST' && url.pathname === '/send') {
    try {
      const body = await parseJsonBody(req);
      const result = await sendAndStoreMessage(body.to, body.text);
      sendResponse(res, 200, JSON.stringify({ ...result.telnyxResponse, message_id: result.messageId }), 'application/json');
    } catch (error) {
      sendResponse(res, 500, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/simulate-inbound') {
    try {
      if (SMS_MODE !== 'simulation') {
        sendResponse(res, 403, JSON.stringify({ error: 'Manual recipient messages are only available in simulation mode' }), 'application/json');
        return;
      }
      const body = await parseJsonBody(req);
      const contact = String(body.from || '').trim();
      const text = String(body.text || '').trim();
      if (!/^\+[1-9]\d{6,14}$/.test(contact)) {
        sendResponse(res, 400, JSON.stringify({ error: 'A valid E.164 recipient is required' }), 'application/json');
        return;
      }
      if (!text) {
        sendResponse(res, 400, JSON.stringify({ error: 'Message text is required' }), 'application/json');
        return;
      }

      const conversation = getOrCreateConversation(contact);
      const telnyxId = `manual-${randomUUID()}`;
      const messageId = addMessage(conversation.id, {
        direction: 'inbound',
        from_number: contact,
        to_number: FIXED_FROM,
        text,
        status: null,
        event_type: 'message.received.manual-simulation',
        telnyx_id: telnyxId
      });
      const message = {
        id: telnyxId,
        from: { phone_number: contact },
        to: [{ phone_number: FIXED_FROM }],
        text,
        simulation: true,
        manual: true
      };
      broadcastEvent('message.received', message);
      const classification = recordInboundClassification(contact, text);
      if (classification?.dnc_alert || classification?.lead_status === 'no_response') {
        updateConversationSettings(contact, { ai_enabled: false });
        markLeadCompleted(contact, classification.dnc_alert ? { lead_status: classification.lead_status, dnc_alert: true } : { lead_status: classification.lead_status });
        broadcastEvent('conversation.updated', { contact, ai_enabled: false });
        if (classification.dnc_alert) {
          sendAndStoreMessage(contact, 'Understood — I’ll remove you from my outreach list. Take care.', 'ai.reply').catch(error => console.error(`DNC reply failed for ${contact}:`, error.message));
        }
      } else {
        scheduleAiReply(contact, text);
      }
      sendResponse(res, 200, JSON.stringify({ ok: true, message_id: messageId }), 'application/json');
    } catch (error) {
      sendResponse(res, 500, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/bulk') {
    try {
      const payload = await parseJsonBody(req);
      if (!Array.isArray(payload)) {
        sendResponse(res, 400, JSON.stringify({ error: 'Expected an array of messages' }), 'application/json');
        return;
      }

      const results = [];
      for (const item of payload) {
        try {
          const r = await sendTelnyxMessage({ from: FIXED_FROM, to: item.to, text: item.text });
          results.push({ success: true, response: r });
        } catch (err) {
          results.push({ success: false, error: err.message });
        }
      }

      sendResponse(res, 200, JSON.stringify({ results }), 'application/json');
    } catch (error) {
      sendResponse(res, 500, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/bulk-outreach') {
    if (SMS_MODE !== 'simulation') {
      sendResponse(res, 403, JSON.stringify({
        error: 'Lead CSV bulk outreach is restricted to SMS_MODE=simulation'
      }), 'application/json');
      return;
    }
    try {
      const payload = await parseJsonBody(req);
      if (!Array.isArray(payload)) {
        sendResponse(res, 400, JSON.stringify({ error: 'Expected an array of leads' }), 'application/json');
        return;
      }
      if (payload.length > 500) {
        sendResponse(res, 400, JSON.stringify({ error: 'A single import cannot exceed 500 leads' }), 'application/json');
        return;
      }

      const results = [];
      for (let index = 0; index < payload.length; index += 1) {
        const item = payload[index] || {};
        const contact = String(item.contact || '').replace(/[\s()-]/g, '');
        const name = String(item.name || '').trim();
        const propertyAddress = String(item.property_address || '').trim();
        const leadSource = String(item.lead_source || '').trim();
        const outreachReason = String(item.outreach_reason || '').trim();
        const initialMessage = String(item.initial_message || '').trim();
        const leadDetails = item.lead_details && typeof item.lead_details === 'object' ? item.lead_details : {};

        try {
          if (!/^\+[1-9]\d{6,14}$/.test(contact)) {
            throw new Error('Invalid E.164 phone number');
          }
          if (!propertyAddress) throw new Error('Property address is required');

          const existing = getConversation(contact);
          if (existing && getMessages(contact).length) {
            throw new Error('Conversation already contains messages');
          }

          getOrCreateConversation(contact, name || null);
          updateConversationSettings(contact, {
            name,
            property_address: propertyAddress,
            ai_enabled: false,
            recipient_ai_enabled: true,
            lead_context: JSON.stringify({
              available: true,
              property_address: propertyAddress,
              lead_source: leadSource,
              outreach_reason: outreachReason,
              phone_number_source: {
                available: false,
                safe_response: 'The imported CSV does not state how the phone number was sourced.'
              },
              property_details: leadDetails
            })
          });
          updateLeadProgress(contact, { queue_status: 'waiting', lead_status: 'processing', dnc_alert: false, meeting_booked: false, processed_at: null });

          const greeting = name ? `Hi ${name.split(/\s+/)[0]}, ` : 'Hi, ';
          const text = initialMessage || `${greeting}I’m reaching out about ${propertyAddress}. Would you consider selling? Bobbie Fisher – RE/MAX`;
          const job = await enqueueLead({ contact, text });
          results.push({
            row: index + 2,
            success: true,
            contact,
            lead_source: leadSource,
            job_id: job.id
          });
        } catch (error) {
          results.push({ row: index + 2, success: false, contact, error: error.message });
        }
      }

      const imported = results.filter(result => result.success).length;
      sendResponse(res, 200, JSON.stringify({
        ok: true,
        imported,
        failed: results.length - imported,
        results
      }), 'application/json');
    } catch (error) {
      sendResponse(res, 400, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/calendar-confirmation') {
    if (SMS_MODE !== 'simulation') {
      sendResponse(res, 403, JSON.stringify({
        error: 'Calendar confirmation endpoint is restricted to simulation mode'
      }), 'application/json');
      return;
    }
    try {
      const body = await parseJsonBody(req);
      const contact = String(body.to || '').replace(/[\s()-]/g, '');
      const text = String(body.text || '').trim();
      if (!/^\+[1-9]\d{6,14}$/.test(contact)) {
        sendResponse(res, 400, JSON.stringify({ error: 'A valid E.164 recipient is required' }), 'application/json');
        return;
      }
      if (!text) {
        sendResponse(res, 400, JSON.stringify({ error: 'Confirmation text is required' }), 'application/json');
        return;
      }
      const telnyxResponse = await sendTelnyxMessage({
        to: contact,
        text,
        suppress_auto_reply: true
      });
      const conversation = getOrCreateConversation(contact);
      const messageId = addMessage(conversation.id, {
        direction: 'outbound',
        from_number: FIXED_FROM,
        to_number: contact,
        text,
        status: 'queued',
        event_type: 'calendar.confirmation',
        telnyx_id: telnyxResponse?.data?.id || null
      });
      broadcastEvent('message.updated', { contact, message_id: messageId });
      sendResponse(res, 200, JSON.stringify({ ok: true, message_id: messageId }), 'application/json');
    } catch (error) {
      sendResponse(res, 500, JSON.stringify({ error: error.message }), 'application/json');
    }
    return;
  }

  if (req.method === 'POST' && url.pathname === '/webhooks') {
    try {
      const payload = await parseJsonBody(req);
      const data = payload?.data;
      console.log('Webhook received:', JSON.stringify(payload));

      const eventType = data?.event_type;
      const message = data?.payload || {};

      if (eventType === 'message.received') {
        // inbound message
        const contact = message.from?.phone_number || message.from || null;
        if (contact) {
          const existingConversation = getConversation(contact);
          const conv = getOrCreateConversation(contact);
          // A simulator-created contact has no UI setup step, so enable its
          // first AI reply. Existing conversations keep their chosen setting.
          if (!existingConversation && message.simulation === true) {
            updateConversationSettings(contact, { ai_enabled: true });
          }
          addMessage(conv.id, {
            direction: 'inbound',
            from_number: message.from?.phone_number || null,
            to_number: Array.isArray(message.to) ? (message.to[0]?.phone_number || null) : message.to || null,
            text: message.text,
            status: null,
            event_type: 'message.received',
            telnyx_id: message.id || null
          });
        }

        broadcastEvent('message.received', message);
        const classification = contact ? recordInboundClassification(contact, message.text || '') : null;
        if (contact && (classification?.dnc_alert || classification?.lead_status === 'no_response')) {
          updateConversationSettings(contact, { ai_enabled: false });
          markLeadCompleted(contact, classification.dnc_alert ? { lead_status: classification.lead_status, dnc_alert: true } : { lead_status: classification.lead_status });
          broadcastEvent('conversation.updated', { contact, ai_enabled: false });
          if (classification.dnc_alert) {
            sendAndStoreMessage(contact, 'Understood — I’ll remove you from my outreach list. Take care.', 'ai.reply').catch(error => console.error(`DNC reply failed for ${contact}:`, error.message));
          }
        } else if (contact) {
          scheduleAiReply(contact, message.text || '');
        }
      } else if (eventType === 'message.sent') {
        // outbound accepted by Telnyx / sent
        const telnyxId = message.id || null;
        if (telnyxId) {
          const existing = findMessageByTelnyxId(telnyxId);
          if (existing) {
            updateMessageStatusByTelnyxId(telnyxId, (message.to && message.to[0]?.status) || 'sent', 'message.sent');
          } else {
            // create outbound message record
            const tonum = Array.isArray(message.to) ? (message.to[0]?.phone_number || null) : message.to || null;
            const conv = getOrCreateConversation(tonum);
            addMessage(conv.id, {
              direction: 'outbound',
              from_number: message.from?.phone_number || null,
              to_number: tounum,
              text: message.text,
              status: (message.to && message.to[0]?.status) || 'sent',
              event_type: 'message.sent',
              telnyx_id: telnyxId
            });
          }
        }

        broadcastEvent('message.updated', message);
      } else if (eventType === 'message.finalized') {
        const telnyxId = message.id || null;
        const status = (message.to && message.to[0]?.status) || null;
        if (telnyxId) {
          updateMessageStatusByTelnyxId(telnyxId, status, 'message.finalized');
        }
        broadcastEvent('message.updated', message);
      }

      sendResponse(res, 200, 'OK');
    } catch (error) {
      console.error('Invalid webhook payload:', error);
      sendResponse(res, 400, 'Invalid JSON');
    }
    return;
  }

  sendResponse(res, 404, 'Not found');
});

server.listen(PORT, () => {
  console.log(`Webhook + chat server running on port ${PORT}`);
});
