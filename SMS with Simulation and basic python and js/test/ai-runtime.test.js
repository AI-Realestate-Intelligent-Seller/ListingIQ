import test from 'node:test';
import assert from 'node:assert/strict';

process.env.AI_API_KEY = 'test-key';
process.env.AI_BASE_URL = 'http://mock-ai.local';
process.env.AI_MODEL = 'test-model';
process.env.BOBBIE_RAG_URL = 'http://mock-ai.local/rag/search';

const { analyzeConversationDisposition, exactOfferedSlot, generateAiReply, resolveCalendarAction, reviewAndRepairAiReply } = await import('../ai-runtime.js');

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' }
  });
}

test('unsafe DeepSeek facts fall back to a grounded commission answer', async () => {
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  globalThis.fetch = async url => {
    if (String(url).endsWith('/rag/search')) {
      return jsonResponse({ matches: [{ text: 'Fees depend on the service and written agreement.' }] });
    }
    completionCalls += 1;
    return jsonResponse({ choices: [{ message: { content: 'I typically charge 2.5%. Email bobbie@example.com for details.' } }] });
  };
  try {
    const reply = await generateAiReply(
      { contact: '+13125550991', property_address: '1 Test St', lead_context: JSON.stringify({ available: true }) },
      [{ direction: 'inbound', text: 'What commission do you charge?' }]
    );
    assert.equal(completionCalls, 2);
    assert.match(reply, /depend on the service and written agreement/i);
    assert.doesNotMatch(reply, /%|@/);
    assert.ok(reply.length <= 240);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('an empty DeepSeek draft is retried once instead of stalling the conversation', async () => {
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  globalThis.fetch = async () => {
    completionCalls += 1;
    const content = completionCalls === 1 ? '' : 'Thanks for asking. What would you like to know?';
    return jsonResponse({ choices: [{ message: { content } }] });
  };
  try {
    const reply = await generateAiReply(
      { contact: '+13125550992', property_address: '2 Test St', lead_context: null },
      [{ direction: 'inbound', text: 'Hello?' }]
    );
    assert.equal(completionCalls, 2);
    assert.equal(reply, 'Thanks for asking. What would you like to know?');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('repeated Bobbie question is replaced with a new direct answer', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({
    choices: [{ message: { content: 'Would a quick call work for you?' } }]
  });
  try {
    const history = [
      { direction: 'outbound', text: 'Would a quick call work for you?' },
      { direction: 'inbound', text: 'I asked for the strategy here by text.' }
    ];
    const reply = await generateAiReply(
      { contact: '+13125550993', property_address: '3 Test St', lead_context: JSON.stringify({ available: true }) },
      history,
      'The recipient declined a call. Keep this to text.'
    );
    assert.doesNotMatch(reply, /quick call/i);
    assert.match(reply, /pricing, presentation, and feedback/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('immediate-buyer objection receives situational guidance and keeps an honest model reply', async () => {
  const inbound = `Do you have a buyer that is ready to move now
It came off the market because it was not moving. I am not interested in
repeating that process. Hence I need someone to present a buyer now.`;
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  let requestBody;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/rag/search')) return jsonResponse({ matches: [] });
    completionCalls += 1;
    requestBody = JSON.parse(options.body);
    return jsonResponse({ choices: [{ message: { content: 'I don’t have a ready buyer today. I understand you won’t repeat the same listing process; would you only consider a direct buyer with proof of funds?' } }] });
  };
  try {
    const reply = await generateAiReply(
      { contact: '+13125550994', property_address: '4 Test St', lead_context: JSON.stringify({ available: true }) },
      [{ direction: 'inbound', text: inbound }],
      '',
      'Final disposition: CONTINUE. Intent: wants_immediate_buyer. The owner is conditionally interested and rejects repeating the previous listing process.'
    );
    assert.equal(completionCalls, 1);
    assert.match(reply, /understand you won’t repeat/i);
    assert.ok(requestBody.messages.some(message => /Intent: wants_immediate_buyer/.test(message.content)));
    assert.equal(requestBody.temperature, 0.4);
    assert.equal(requestBody.max_tokens, 120);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('DeepSeek disposition continues conditional interest and ends a clear rejection', async () => {
  const originalFetch = globalThis.fetch;
  const responses = [
    { action: 'continue', intent: 'wants_immediate_buyer', lead_status: 'interested', confidence: 0.98, next_step: 'answer_and_qualify', qualification_focus: 'what prevented buyer commitment during the prior listing', calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'No current call consent' }, reason: 'Owner sets a condition and asks a question.' },
    { action: 'end', intent: 'direct_rejection', lead_status: 'not_interested', confidence: 0.99, next_step: 'close', qualification_focus: '', calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'Conversation ending' }, reason: 'Owner clearly rejects further discussion.' }
  ];
  globalThis.fetch = async () => jsonResponse({ choices: [{ message: { content: JSON.stringify(responses.shift()) } }] });
  try {
    const conversation = { contact: '+13125550995', property_address: '5 Test St' };
    const continuing = await analyzeConversationDisposition(conversation, [
      { direction: 'outbound', text: 'Would you consider selling?' },
      { direction: 'inbound', text: 'I will not repeat the listing process. Do you have a buyer ready now?' }
    ]);
    assert.equal(continuing.action, 'continue');
    assert.equal(continuing.intent, 'wants_immediate_buyer');
    assert.equal(continuing.lead_status, 'interested');
    assert.equal(continuing.next_step, 'answer_and_qualify');
    assert.match(continuing.qualification_focus, /prevented buyer commitment/i);
    assert.equal(continuing.calendar.state, 'none');
    assert.equal(continuing.calendar.should_fetch_availability, false);

    const ending = await analyzeConversationDisposition(conversation, [
      { direction: 'outbound', text: 'Would you consider selling?' },
      { direction: 'inbound', text: 'No. I am not interested in selling or discussing this further.' }
    ]);
    assert.equal(ending.action, 'end');
    assert.equal(ending.lead_status, 'not_interested');
    assert.equal(ending.next_step, 'close');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('disposition provider failure falls back without throwing', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('provider unavailable'); };
  try {
    const result = await analyzeConversationDisposition(
      { contact: '+13125550996', property_address: '6 Test St' },
      [{ direction: 'inbound', text: 'Do you have a buyer ready now? I need someone to present one.' }]
    );
    assert.equal(result.action, 'continue');
    assert.equal(result.lead_status, 'interested');
    assert.equal(result.calendar.state, 'none');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('polite goodbye closes messaging without erasing ready-to-sell analytics', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({ choices: [{ message: { content: JSON.stringify({
    action: 'end',
    intent: 'polite_goodbye_after_qualification',
    outcome: 'positive',
    lead_status: 'ready_to_sell',
    confidence: 0.99,
    next_step: 'close',
    qualification_focus: '',
    calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'No scheduling request' },
    reason: 'The owner is ready to sell and is only ending the current text exchange.'
  }) } }] });
  try {
    const result = await analyzeConversationDisposition(
      { contact: '+13125550960', property_address: '9 Test St', lead_status: 'ready_to_sell' },
      [
        { direction: 'inbound', text: 'Everything is clear and I am ready to sell.' },
        { direction: 'outbound', text: 'Thanks for confirming.' },
        { direction: 'inbound', text: 'ok bye' }
      ]
    );
    assert.equal(result.action, 'end');
    assert.equal(result.outcome, 'positive');
    assert.equal(result.lead_status, 'ready_to_sell');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('structured qualification readiness advances Bobbie to a call instead of more questions', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({ choices: [{ message: { content: JSON.stringify({
    action: 'continue',
    intent: 'ready_seller',
    outcome: 'positive',
    lead_status: 'ready_to_sell',
    confidence: 0.99,
    conversation_stage: 'qualified_for_call',
    next_step: 'answer_and_qualify',
    qualification_focus: 'property documents',
    calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'A call has not been requested yet' },
    reason: 'The owner is willing to sell, flexible, and has supplied price and timing.'
  }) } }] });
  try {
    const result = await analyzeConversationDisposition(
      { contact: '+13125550961', property_address: '10 Test St', lead_status: 'interested' },
      [
        { direction: 'outbound', text: 'What price were you hoping for?' },
        { direction: 'inbound', text: 'Around $300K, and I am flexible on timing.' }
      ]
    );
    assert.equal(result.conversation_stage, 'qualified_for_call');
    assert.equal(result.next_step, 'offer_call');
    assert.equal(result.qualification_focus, '');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('offer-call policy fallback never leaks the generic verification dead end', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({
    choices: [{ message: { content: 'What timeline are you expecting?' } }]
  });
  try {
    const history = [
      { direction: 'outbound', text: 'What timeline are you expecting?' },
      { direction: 'inbound', text: 'Everything is ready and I want to sell within 2-3 weeks.' }
    ];
    const reply = await generateAiReply(
      { contact: '+13125550962', property_address: '11 Test St', lead_context: JSON.stringify({ available: true }) },
      history,
      '',
      'Required next step: offer_call. Do not qualify further.',
      { next_step: 'offer_call' }
    );
    assert.match(reply, /quick 5–10 minute call/i);
    assert.doesNotMatch(reply, /shared what I can verify/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a yes to the latest buyer question does not activate an older call invitation', async () => {
  const originalFetch = globalThis.fetch;
  let requestBody;
  globalThis.fetch = async (url, options = {}) => {
    requestBody = JSON.parse(options.body);
    return jsonResponse({ choices: [{ message: { content: JSON.stringify({
      action: 'continue',
      intent: 'asks_if_buyer_is_available',
      lead_status: 'interested',
      confidence: 0.99,
      next_step: 'answer_and_qualify',
      qualification_focus: 'feedback from the prior listing',
      calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'Yes answers the immediately preceding proof-of-funds buyer question' },
      reason: 'The owner asks whether that buyer exists; no current call was offered.'
    }) } }] });
  };
  try {
    const result = await analyzeConversationDisposition(
      { contact: '+13125550997', property_address: '7 Test St' },
      [
        { direction: 'outbound', text: 'Would you be open to a brief conversation?' },
        { direction: 'inbound', text: 'Do you have a buyer ready now? I will not repeat the listing process.' },
        { direction: 'outbound', text: 'Would you only consider a direct buyer with proof of funds?' },
        { direction: 'inbound', text: 'yes, do you have that?' }
      ]
    );
    assert.equal(result.calendar.state, 'none');
    assert.equal(result.next_step, 'answer_and_qualify');
    assert.match(requestBody.messages[0].content, /immediately preceding question only/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('an owner-proposed time needs confirmation but selecting an offered slot books immediately', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({ choices: [{ message: { content: JSON.stringify({
    action: 'book',
    start_at: '2026-08-10T14:30:00Z',
    end_at: '2026-08-10T15:00:00Z',
    reason: 'The requested slot is available.'
  }) } }] });
  const availability = {
    current_time: '2026-08-07T11:45:00-05:00',
    timezone: 'America/Chicago',
    slots: [{ start_at: '2026-08-10T14:30:00Z', end_at: '2026-08-10T15:00:00Z', label: 'Monday, August 10, 2026 at 09:30 AM America/Chicago' }]
  };
  try {
    const proposed = await resolveCalendarAction(
      { contact: '+13125550998' },
      [{ direction: 'inbound', text: 'Monday at 9:30 AM works.' }],
      availability,
      { state: 'time_proposed', requested_time_text: 'Monday at 9:30 AM' }
    );
    assert.equal(proposed.action, 'ask_confirmation');

    const confirmed = await resolveCalendarAction(
      { contact: '+13125550998' },
      [
        { direction: 'outbound', text: 'I can do Monday at 9:30 AM or Tuesday at 1:00 PM. Which works?' },
        { direction: 'inbound', text: 'Monday at 9:30 AM works.' }
      ],
      availability,
      { state: 'booking_confirmed', requested_time_text: 'Monday at 9:30 AM' }
    );
    assert.equal(confirmed.action, 'book');
    assert.equal(confirmed.start_at, '2026-08-10T14:30:00Z');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('exact selection from the immediately preceding live options resolves without another AI call', async () => {
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  globalThis.fetch = async () => {
    completionCalls += 1;
    return jsonResponse({ choices: [{ message: { content: '' } }] });
  };
  const availability = {
    current_time: '2026-08-10T06:30:00-05:00',
    timezone: 'America/Chicago',
    slots: [
      { start_at: '2026-08-10T14:30:00Z', end_at: '2026-08-10T15:00:00Z', label: 'Monday, August 10, 2026 at 09:30 AM America/Chicago' },
      { start_at: '2026-08-11T14:00:00Z', end_at: '2026-08-11T14:30:00Z', label: 'Tuesday, August 11, 2026 at 09:00 AM America/Chicago' }
    ]
  };
  const history = [
    { direction: 'outbound', text: 'I can do Monday, August 10 at 09:30 AM or Tuesday, August 11 at 09:00 AM. Which works?' },
    { direction: 'inbound', text: 'August 10 at 09:30 AM' }
  ];
  try {
    assert.equal(exactOfferedSlot(history, availability)?.start_at, '2026-08-10T14:30:00Z');
    const result = await resolveCalendarAction(
      { contact: '+17481592635' },
      history,
      availability,
      { state: 'booking_confirmed', requested_time_text: 'August 10 at 09:30 AM' }
    );
    assert.equal(result.action, 'book');
    assert.equal(result.consent, 'offered_slot_selected');
    assert.equal(result.start_at, '2026-08-10T14:30:00Z');
    assert.equal(completionCalls, 0);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('calendar resolver failure never claims that an unverified selection is unavailable', async () => {
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  globalThis.fetch = async () => {
    completionCalls += 1;
    return jsonResponse({ choices: [{ message: { content: '' } }] });
  };
  try {
    const result = await resolveCalendarAction(
      { contact: '+17481592636' },
      [
        { direction: 'outbound', text: 'What day works for you?' },
        { direction: 'inbound', text: 'The same time we discussed.' }
      ],
      { current_time: '2026-08-10T06:30:00-05:00', timezone: 'America/Chicago', slots: [] },
      { state: 'booking_confirmed', requested_time_text: 'the same time' }
    );
    assert.equal(completionCalls, 2);
    assert.equal(result.action, 'resolution_failed');
    assert.doesNotMatch(result.reason, /not currently available/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('calendar resolver can recover when disposition mislabels an offered-slot selection', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({ choices: [{ message: { content: JSON.stringify({
    action: 'book',
    consent: 'offered_slot_selected',
    start_at: '2026-08-10T18:00:00Z',
    end_at: '2026-08-10T18:30:00Z',
    reason: 'The owner selected Bobbie\'s offered 1 PM slot.'
  }) } }] });
  const availability = {
    current_time: '2026-08-08T12:00:00-05:00',
    timezone: 'America/Chicago',
    slots: [{ start_at: '2026-08-10T18:00:00Z', end_at: '2026-08-10T18:30:00Z', label: 'Monday, August 10, 2026 at 01:00 PM America/Chicago' }]
  };
  try {
    const result = await resolveCalendarAction(
      { contact: '+13125550964' },
      [
        { direction: 'outbound', text: 'Monday at 9:00 AM, 10:30 AM, or 1:00 PM—which works?' },
        { direction: 'inbound', text: 'I think 1 PM.' }
      ],
      availability,
      { state: 'time_proposed', requested_time_text: '1 PM' }
    );
    assert.equal(result.consent, 'offered_slot_selected');
    assert.equal(result.action, 'book');
    assert.equal(result.start_at, '2026-08-10T18:00:00Z');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('disposition instructions treat selection from live options as booking consent', async () => {
  const originalFetch = globalThis.fetch;
  let requestBody;
  globalThis.fetch = async (url, options = {}) => {
    requestBody = JSON.parse(options.body);
    return jsonResponse({ choices: [{ message: { content: JSON.stringify({
      action: 'continue',
      intent: 'selects_offered_slot',
      outcome: 'positive',
      lead_status: 'ready_to_sell',
      confidence: 0.99,
      conversation_stage: 'scheduling',
      next_step: 'schedule',
      qualification_focus: '',
      calendar: { state: 'booking_confirmed', requested_time_text: '1 PM', reason: 'Owner selected an offered slot' },
      reason: 'The owner selected one of Bobbie\'s live options.'
    }) } }] });
  };
  try {
    const result = await analyzeConversationDisposition(
      { contact: '+13125550963', property_address: '12 Test St', lead_status: 'ready_to_sell' },
      [
        { direction: 'outbound', text: 'Monday at 9:00 AM, 10:30 AM, or 1:00 PM—which works?' },
        { direction: 'inbound', text: 'I think 1 PM.' }
      ]
    );
    assert.equal(result.calendar.state, 'booking_confirmed');
    assert.equal(result.calendar.should_fetch_availability, true);
    assert.match(requestBody.messages[0].content, /Selecting an offered option is sufficient consent/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('semantic reviewer rewrites a premature calendar reply into the expected answer', async () => {
  const originalFetch = globalThis.fetch;
  let completionCalls = 0;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).endsWith('/rag/search')) return jsonResponse({ matches: [] });
    const body = JSON.parse(options.body);
    if (/independent semantic QA reviewer/i.test(body.messages[0].content)) {
      return jsonResponse({ choices: [{ message: { content: JSON.stringify({
        valid: false,
        issues: ['premature_scheduling', 'did_not_answer_buyer_question'],
        rewrite_instruction: 'Say no verified buyer is available and ask about feedback from the prior listing; do not mention time slots.',
        reason: 'The owner asked about a buyer, not scheduling.'
      }) } }] });
    }
    completionCalls += 1;
    return jsonResponse({ choices: [{ message: { content: 'I don’t have a verified buyer right now. What feedback did your previous agent give about why the property didn’t sell?' } }] });
  };
  try {
    const conversation = { contact: '+13125550999', property_address: '8 Test St', lead_context: JSON.stringify({ available: true }) };
    const history = [
      { direction: 'outbound', text: 'Would you only consider a direct buyer with proof of funds?' },
      { direction: 'inbound', text: 'yes, do you have that?' }
    ];
    const disposition = {
      action: 'continue', intent: 'asks_if_buyer_is_available', next_step: 'answer_and_qualify', qualification_focus: 'prior listing feedback',
      calendar: { state: 'none', should_fetch_availability: false, requested_time_text: '', reason: 'No call consent' }
    };
    const reply = await reviewAndRepairAiReply(
      conversation,
      history,
      'I can do Friday at 12:30 PM. Which works?',
      disposition,
      '',
      'Answer the buyer question and qualify the failed listing.'
    );
    assert.equal(completionCalls, 1);
    assert.match(reply, /don’t have a verified buyer/i);
    assert.match(reply, /previous agent/i);
    assert.doesNotMatch(reply, /Friday|12:30/i);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
