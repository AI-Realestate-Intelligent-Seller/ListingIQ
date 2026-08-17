const EMAIL_ADDRESS = /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i;
const PHONE_NUMBER = /(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b/;
const DELIVERY_PROMISE = /\b(?:i['’]?ll|i will|i can(?!['’]?t|\s+not)|we['’]?ll|we will|have Bobbie)\b[^.!?]{0,90}\b(?:email|send|pull|get|prepare|share)\b[^.!?]{0,90}\b(?:email|comps?|comparables?|recent sales|sales data|details|breakdown|range|estimate|report)\b|\b(?:i['’]?ll|i will) (?:send|get) (?:that|those|them|it) (?:over|to you)\b|\b(?:i['’]?ll|i will) (?:be in touch|reach out|get back|follow up)\b|\b(?:pull|review|handle)\b[^.!?]{0,80}\b(?:on my end|separately|get back to you|be in touch)\b/i;
const UNSUPPORTED_FEE = /(?:\b\d+(?:\.\d+)?\s*%|\bno upfront fees?\b|\bcommission only\b|\btypically (?:charge|work).{0,30}\bcommission\b)/i;
const UNSUPPORTED_BUYER = /\b(?:active|specific|ready|qualified|cash) buyers?\b|\bnetwork of buyers?\b|\bbuyers? (?:actively )?(?:looking|searching)\b/i;
const BUYER_DENIAL = /\b(?:i|we)\s+(?:do not|don['’]?t)\s+(?:currently\s+)?have\s+(?:a\s+)?(?:verified|specific|ready|qualified|cash|active)?\s*buyer\b|\bthere (?:is|are) no\s+(?:verified|specific|ready|qualified|cash|active)?\s*buyers?\b/gi;
const UNSUPPORTED_PRODUCTION = /\b(?:my|our) recent sales\b|\b(?:i|we)(?:['’]?ve| have) (?:sold|closed|handled)\b/i;
const UNSUPPORTED_LOCAL_EXPERIENCE = /\b(?:i['’]?ve (?:worked|helped)|i (?:work|have worked) with|i know the [^.?!]{0,35} market|my local experience)\b/i;
const PRECISE_TENURE = /\b(?:several years|since \d{4}|(?:for|over) \d+ years)\b/i;
const EXAMPLE_FACT_LEAK = /\b(?:sliding glass doors?|100 showings?|investor['’]?s note|27620 E Lakeview|Morgan|Grant|Dolby Haas)\b/i;
const UNSUPPORTED_OUTCOME = /\b(?:get(?:ting)? homes? sold efficiently|generate real offers? quickly|sell for the right price|attract the right buyer quickly)\b/i;
const STOP_WORDS = new Set(['a', 'about', 'and', 'are', 'at', 'be', 'for', 'have', 'i', 'if', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'or', 'the', 'this', 'to', 'we', 'what', 'with', 'would', 'you', 'your']);

function cleanReply(value = '') {
  return String(value)
    .replace(/^(?:["'“”])|(?:["'“”])$/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function fitCompleteSms(value, maxLength = 240) {
  const text = cleanReply(value);
  if (text.length <= maxLength) return text;
  const prefix = text.slice(0, maxLength);
  const sentenceEnd = Math.max(prefix.lastIndexOf('.'), prefix.lastIndexOf('!'), prefix.lastIndexOf('?'));
  if (sentenceEnd >= 80) return prefix.slice(0, sentenceEnd + 1).trim();
  const wordEnd = prefix.lastIndexOf(' ');
  return `${prefix.slice(0, Math.max(1, wordEnd)).replace(/[,:;\s-]+$/, '')}…`;
}

function normalizedTokens(value = '') {
  return cleanReply(value).toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter(token => token.length > 1 && !STOP_WORDS.has(token));
}

function similarity(left = '', right = '') {
  const a = new Set(normalizedTokens(left));
  const b = new Set(normalizedTokens(right));
  if (!a.size || !b.size) return 0;
  const shared = [...a].filter(token => b.has(token)).length;
  return shared / Math.max(a.size, b.size);
}

function questions(value = '') {
  const text = cleanReply(value);
  const found = [];
  const pattern = /(?:^|[.!])\s*([^.!?]*\?)/g;
  for (const match of text.matchAll(pattern)) found.push(match[1]);
  return found;
}

function questionIntent(value = '') {
  const text = String(value).toLowerCase();
  if (/\b(?:call|phone|meet|meeting|appointment)\b/.test(text)) return 'scheduled_meeting';
  if (/\b(?:chat|talk|conversation)\b/.test(text)) return 'conversation';
  if (/\b(?:timeline|when|how soon|timeframe)\b/.test(text)) return 'timeline';
  if (/\b(?:goal|priority|important|matter most)\b/.test(text)) return 'goals';
  if (/\b(?:fee|commission|rate|percentage)\b/.test(text)) return 'fees';
  if (/\b(?:price|pricing|value|worth)\b/.test(text)) return 'pricing';
  if (/\b(?:buyer|offer)\b/.test(text)) return 'buyer';
  return '';
}

function findConversationRepetition(reply, history = []) {
  const prior = history.filter(item => item.direction === 'outbound').map(item => cleanReply(item.text || '')).filter(Boolean);
  const current = cleanReply(reply);
  const currentQuestions = questions(current);
  for (const previous of prior) {
    if (current.toLowerCase() === previous.toLowerCase()) return 'repeated_reply';
    if (normalizedTokens(current).length >= 6 && similarity(current, previous) >= 0.86) return 'repeated_reply';
    for (const currentQuestion of currentQuestions) {
      for (const previousQuestion of questions(previous)) {
        const sameIntent = questionIntent(currentQuestion) && questionIntent(currentQuestion) === questionIntent(previousQuestion);
        if (similarity(currentQuestion, previousQuestion) >= 0.72 || (sameIntent && questionIntent(currentQuestion) === 'scheduled_meeting')) {
          return 'repeated_question';
        }
      }
    }
  }
  return '';
}

function findReplyPolicyViolations(reply, options = {}) {
  const text = cleanReply(reply);
  const violations = [];
  if (text.length > (options.maxLength || 240)) violations.push('over_length');
  if (/\bBobbie Fisher['’]?s (?:AI )?assistant\b/i.test(text)) violations.push('identity_switch');
  if (/\bBobbie\s+(?:would|will|can|needs?|has|focuses|is)\b|\b(?:she|her)\s+(?:would|will|can|needs?|has|focuses|is)\b/i.test(text)) violations.push('identity_switch');
  if (EMAIL_ADDRESS.test(text)) violations.push('invented_email');
  if (PHONE_NUMBER.test(text)) violations.push('invented_phone');
  if (DELIVERY_PROMISE.test(text)) violations.push('unsupported_delivery');
  if (UNSUPPORTED_FEE.test(text)) violations.push('unsupported_fee');
  // Honest denials such as "I don't have a ready buyer" are safe. Remove only
  // those clauses, then inspect the remainder for an unsupported positive claim.
  if (UNSUPPORTED_BUYER.test(text.replace(BUYER_DENIAL, ' '))) violations.push('unsupported_buyer');
  if (UNSUPPORTED_PRODUCTION.test(text)) violations.push('unsupported_production');
  if (UNSUPPORTED_LOCAL_EXPERIENCE.test(text)) violations.push('unsupported_local_experience');
  if (UNSUPPORTED_OUTCOME.test(text)) violations.push('unsupported_outcome');
  if (PRECISE_TENURE.test(text)) violations.push('unsupported_tenure');
  if (EXAMPLE_FACT_LEAK.test(text)) violations.push('example_fact_leak');
  if (
    options.phoneSourceKnown === false
    && /\b(?:number|contact info|contact information)\b/i.test(text)
    && /\b(?:public|property|listing|county|tax)\s+(?:record|records|data)\b/i.test(text)
  ) violations.push('unsupported_phone_source');
  const repetition = findConversationRepetition(text, options.history || []);
  if (repetition) violations.push(repetition);
  return [...new Set(violations)];
}

function safeGroundedFallback(latestInbound = '', violations = [], history = [], plan = {}) {
  const latest = String(latestInbound);
  const candidates = [];
  if (plan.next_step === 'offer_call') candidates.push('Thanks—that gives me enough to understand what you need. Would you be open to a quick 5–10 minute call to discuss the next step?');
  if (violations.includes('unsupported_phone_source') || /\b(?:how did you (?:find|get)|where did you get|got|found)\b[^?]{0,45}\b(?:my |the )?(?:number|contact info)|\bmy (?:number|contact info)\b/i.test(latest)) candidates.push('I have a property lead record, but it doesn’t show how your phone number was sourced, so I don’t want to guess.');
  if (violations.includes('invented_phone') || /\b(?:your (?:number|phone)|call you|reach you)\b/i.test(latest)) candidates.push('I don’t have a verified callback number to provide in this chat; scheduling has to use the live calendar.');
  if (violations.includes('unsupported_fee') || /\b(?:commission|fee|percentage|rate|listing term)\b/i.test(latest)) candidates.push('Fees and listing terms depend on the service and written agreement, so I don’t have approved numbers to quote here.');
  if (violations.includes('unsupported_delivery') || /\b(?:email|comps?|recent sales|sales data|estimate|report)\b/i.test(latest)) candidates.push('This chat can’t retrieve verified comps or perform a later follow-up; those records need a separate human review.');
  if (violations.includes('unsupported_buyer') || /\bbuyers?\b/i.test(latest)) candidates.push('I don’t have a verified buyer ready today. I understand you won’t repeat the same listing process; would you only consider a direct buyer with proof of funds?');
  if (/\b(?:fresh approach|strategy|plan|options?|different|highlights?)\b/i.test(latest) || violations.includes('example_fact_leak')) candidates.push('I’d review the prior pricing, presentation, and feedback to identify what kept buyers from acting before suggesting a new approach.');
  if (/\b(?:why|what made|what brought|think of me|specific (?:place|property)|reach out)\b/i.test(latest)) candidates.push('That expired-listing signal is the only verified reason I have; I don’t have another property-specific trigger to claim.');
  if (/\b(?:price|pricing|value|worth)\b/i.test(latest)) candidates.push('I’d compare the prior price with current competition and buyer feedback before recommending a position.');
  if (/\b(?:timeline|timeframe|how long)\b/i.test(latest)) candidates.push('Timing depends on price, condition, and current demand, so I don’t have verified data to promise a timeframe.');
  if (/\bnext step\b/i.test(latest)) candidates.push('The next step would be a human review of the prior listing and current market data; this chat can’t retrieve those records.');
  candidates.push('I’ve shared what I can verify here, and I don’t want to repeat myself or invent details.');
  return candidates.find(candidate => !findConversationRepetition(candidate, history)) || 'I don’t have anything new and verified to add, so I’ll leave it there.';
}

export { cleanReply, findConversationRepetition, findReplyPolicyViolations, fitCompleteSms, safeGroundedFallback };
