import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyLeadMessage, finalizedLeadStatus, mergeLeadStatus } from '../lead-classifier.js';

test('generic closing does not erase a useful classification', () => {
  assert.equal(classifyLeadMessage('Thanks, I’ll keep an eye out.'), null);
  assert.equal(mergeLeadStatus('want_more_info', null), 'want_more_info');
});

test('curly apostrophe pass is terminal not interested', () => {
  assert.deepEqual(classifyLeadMessage('If you cannot provide that, I’ll pass for now.'), {
    lead_status: 'not_interested',
    terminal: true
  });
});

test('stronger positive intent is not downgraded by a later information request', () => {
  assert.equal(mergeLeadStatus('interested', 'want_more_info'), 'interested');
});

test('hypothetical interest is not treated as actual interest', () => {
  assert.equal(classifyLeadMessage('What would the process be if I were interested?'), null);
});

test('DNC overrides every earlier status', () => {
  assert.equal(mergeLeadStatus('ready_to_sell', 'dnc'), 'dnc');
});

test('completed unclassified lead does not remain processing', () => {
  assert.equal(finalizedLeadStatus('processing'), 'not_interested');
});

test('declining only the call does not terminate the whole lead', () => {
  assert.equal(classifyLeadMessage('I’m not interested in a call, but can you text recent sales?')?.lead_status, 'want_more_info');
  assert.equal(classifyLeadMessage('I’ll hold off for now until I see the comps.')?.lead_status, 'want_more_info');
});

test('accepted call is interested and fee request is want more info', () => {
  assert.equal(classifyLeadMessage('A quick call could work tomorrow.')?.lead_status, 'interested');
  assert.equal(classifyLeadMessage('What is your fee and listing term?')?.lead_status, 'want_more_info');
});

test('a relisting objection with an immediate-buyer condition stays active', () => {
  const reply = `Do you have a buyer that is ready to move now
It came off the market because it was not moving. I am not interested in
repeating that process.
Hence I need someone to present a buyer now.`;

  assert.deepEqual(classifyLeadMessage(reply), { lead_status: 'interested' });
});

test('a direct not-interested reply remains terminal', () => {
  assert.deepEqual(classifyLeadMessage('I am not interested in selling. Thanks.'), {
    lead_status: 'not_interested',
    terminal: true
  });
});

test('rejecting the previous listing process alone is not a terminal rejection', () => {
  assert.equal(classifyLeadMessage('I am not interested in repeating that process.'), null);
});
