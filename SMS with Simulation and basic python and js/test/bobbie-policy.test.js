import test from 'node:test';
import assert from 'node:assert/strict';
import { findConversationRepetition, findReplyPolicyViolations, fitCompleteSms, safeGroundedFallback } from '../bobbie-policy.js';

test('long SMS is shortened at a complete sentence instead of mid-word', () => {
  const reply = `${'A'.repeat(100)}. ${'B'.repeat(180)}.`;
  const fitted = fitCompleteSms(reply, 160);
  assert.equal(fitted, `${'A'.repeat(100)}.`);
  assert.ok(fitted.length <= 160);
});

test('detects unsupported commission, email, buyer, and phone-source claims', () => {
  assert.ok(findReplyPolicyViolations('I typically charge 2.5%.', { phoneSourceKnown: false }).includes('unsupported_fee'));
  assert.ok(findReplyPolicyViolations('Email me at bobbie@example.com.', { phoneSourceKnown: false }).includes('invented_email'));
  assert.ok(findReplyPolicyViolations('I have active buyers looking now.', { phoneSourceKnown: false }).includes('unsupported_buyer'));
  assert.ok(findReplyPolicyViolations('I pulled your number from public property records.', { phoneSourceKnown: false }).includes('unsupported_phone_source'));
  assert.ok(findReplyPolicyViolations('Your contact info came from public property records.', { phoneSourceKnown: false }).includes('unsupported_phone_source'));
  assert.ok(findReplyPolicyViolations('I work with sellers throughout Mattoon.', { phoneSourceKnown: false }).includes('unsupported_local_experience'));
  assert.ok(findReplyPolicyViolations('Call me at 312-555-0199.', { phoneSourceKnown: false }).includes('invented_phone'));
});

test('allows an honest buyer denial but still blocks a positive buyer claim', () => {
  assert.ok(!findReplyPolicyViolations('I don’t have a ready buyer today.', {}).includes('unsupported_buyer'));
  assert.ok(!findReplyPolicyViolations('There is no specific buyer verified for this property.', {}).includes('unsupported_buyer'));
  assert.ok(findReplyPolicyViolations('I don’t have a ready buyer, but I have active buyers looking nearby.', {}).includes('unsupported_buyer'));
});

test('blocks promises to send unavailable comps or email', () => {
  const violations = findReplyPolicyViolations("I'll email the recent comps shortly.", { phoneSourceKnown: false });
  assert.ok(violations.includes('unsupported_delivery'));
});

test('grounded fallback answers an unknown phone source honestly', () => {
  assert.match(safeGroundedFallback('How did you get my number?', []), /doesn’t show how your phone number was sourced/i);
  assert.match(safeGroundedFallback('What’s your number so I can call you?', []), /verified callback number/i);
});

test('detects exact replies and paraphrased repeated call questions', () => {
  const exactHistory = [{ direction: 'outbound', text: 'I do not have a verified buyer for your property.' }];
  assert.equal(findConversationRepetition('I do not have a verified buyer for your property.', exactHistory), 'repeated_reply');
  const callHistory = [{ direction: 'outbound', text: 'Would a quick call work for you?' }];
  assert.equal(findConversationRepetition('Do you have 10 minutes for a quick call?', callHistory), 'repeated_question');
  assert.equal(findConversationRepetition('Would you be open to a brief meeting?', callHistory), 'repeated_question');
});

test('does not confuse an earlier text conversation invitation with a later phone call offer', () => {
  const history = [
    { direction: 'outbound', text: 'Would you be open to a brief conversation about the property?' },
    { direction: 'outbound', text: "I'm just checking if you'd be open to a quick chat about the property and your options." },
    { direction: 'outbound', text: 'What would your ideal timeline look like if you could sell on your terms?' },
    { direction: 'inbound', text: 'I am flexible, but not more than two months.' }
  ];
  const reply = 'Thanks—that gives me enough to understand what you need. Would you be open to a quick 5–10 minute call to discuss the next step?';
  assert.equal(findConversationRepetition(reply, history), '');
  assert.ok(!findReplyPolicyViolations(reply, { history }).includes('repeated_question'));
});

test('blocks future follow-up promises and raw example facts', () => {
  assert.ok(findReplyPolicyViolations("I'll be in touch once I have the details.", {}).includes('unsupported_delivery'));
  assert.ok(findReplyPolicyViolations("The investor's note about the sliding glass doors is important.", {}).includes('example_fact_leak'));
});

test('capability denial is allowed and unrelated later question is not a repeated call ask', () => {
  assert.ok(!findReplyPolicyViolations('I can’t send email or retrieve verified comps from this chat.', {}).includes('unsupported_delivery'));
  const history = [{ direction: 'outbound', text: 'Would a quick call work for you?' }];
  const reply = "I can explain the approach here by text. What's most important to you?";
  assert.equal(findConversationRepetition(reply, history), '');
});

test('grounded fallback does not repeat a fallback already sent', () => {
  const previous = 'I don’t have a verified buyer for your property; my outreach is to learn whether selling is still an option.';
  const fallback = safeGroundedFallback('What kind of buyer do you have?', ['unsupported_buyer'], [{ direction: 'outbound', text: previous }]);
  assert.notEqual(fallback, previous);
});
