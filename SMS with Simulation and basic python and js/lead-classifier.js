const TERMINAL_STATUSES = new Set(['dnc', 'not_interested', 'no_response']);
const STATUS_PRIORITY = {
  processing: 0,
  want_more_info: 1,
  interested: 2,
  ready_to_sell: 3
};

function isOptOut(text = '') {
  return /\b(stop|stopall|unsubscribe|remove me|do not contact|don['’]?t contact|don['’]?t message|leave me alone|take me off|done here)\b/i.test(String(text).trim());
}

function hasImmediateBuyerCondition(text = '') {
  const value = String(text).trim();
  return /\b(?:do you|you|someone)\b.{0,45}\b(?:have|bring|present|provide)\b.{0,25}\b(?:a\s+)?(?:ready\s+)?buyer\b/i.test(value)
    || /\b(?:need|want)\b.{0,45}\b(?:present|bring|provide|find)\b.{0,25}\b(?:a\s+)?buyer\b/i.test(value)
    || /\b(?:buyer|offer)\b.{0,35}\b(?:ready|immediate(?:ly)?|move now|right now)\b/i.test(value);
}

function isQualifiedProcessObjection(text = '') {
  return /\bnot interested\b\s+(?:in|with)\s+(?:(?:repeating|restarting|reliving)\s+(?:that|the|this)\s+process|going through (?:that|the|this) process|listing again|relisting)\b/i.test(String(text).trim());
}

function classifyLeadMessage(text = '') {
  const value = String(text).trim();
  if (!value) return null;
  if (/^\(?no reply\)?$/i.test(value)) return { lead_status: 'no_response', terminal: true };
  if (isOptOut(value)) return { lead_status: 'dnc', dnc_alert: true, terminal: true };
  // A seller can reject relisting while still expressing a concrete condition
  // under which they would engage. Do not let the words "not interested"
  // erase that stronger, actionable signal.
  if (hasImmediateBuyerCondition(value)) return { lead_status: 'interested' };
  const directRejection = /\bnot interested\b(?!\s+(?:in|with)\s+(?:a\s+)?(?:call|meeting|phone conversation))|\b(?:have to pass|i(?:['’]ll| will) pass|already sold|wrong number)\b/i.test(value);
  if (directRejection && !isQualifiedProcessObjection(value)) {
    return { lead_status: 'not_interested', terminal: true };
  }
  if (/\b(?:need to sell|ready to sell|sell soon|as soon as possible|immediately)\b/i.test(value)) {
    return { lead_status: 'ready_to_sell' };
  }
  if (/\b(?:open to|might sell|consider(?:ing)? selling|(?:i(?:['’]m| am)|we(?:['’]re| are)) (?:still )?interested|right price)\b/i.test(value)) {
    return { lead_status: 'interested' };
  }
  if (/\b(?:quick call (?:could|would|might) work|open to a (?:quick )?(?:call|meeting))\b/i.test(value)) {
    return { lead_status: 'interested' };
  }
  if (/\b(?:email|send (?:me )?(?:info|information|details)|more info|recent sales|comps?|ballpark|estimate|price range|commission|fees?|listing terms?|strategy|plan|options?|what are you proposing)\b/i.test(value)) {
    return { lead_status: 'want_more_info' };
  }
  return null;
}

function mergeLeadStatus(current = 'processing', next = null) {
  if (!next) return current || 'processing';
  if (next === 'dnc') return next;
  if (TERMINAL_STATUSES.has(current)) return current;
  if (TERMINAL_STATUSES.has(next)) return next;
  return (STATUS_PRIORITY[next] ?? 0) >= (STATUS_PRIORITY[current] ?? 0) ? next : current;
}

function finalizedLeadStatus(status = 'processing') {
  return !status || status === 'processing' ? 'not_interested' : status;
}

export { classifyLeadMessage, finalizedLeadStatus, isOptOut, mergeLeadStatus };
