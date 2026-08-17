import test from 'node:test';
import assert from 'node:assert/strict';
import { containsSchedulingPressure, containsTimeProposal, containsUnbookedConfirmation } from '../scheduling-state.js';

test('pressure guard catches alternate call wording and direct time offers', () => {
  assert.equal(containsSchedulingPressure('A quick conversation would help.'), true);
  assert.equal(containsSchedulingPressure('I can do Friday at 1:00 PM.'), true);
  assert.equal(containsSchedulingPressure('I’ll keep this to text.'), false);
});

test('calendar guards catch invented offers and unbooked confirmations', () => {
  assert.equal(containsTimeProposal('I have time tomorrow morning—would 10 AM work?'), true);
  assert.equal(containsUnbookedConfirmation("Perfect, I'll give you a call at 10 AM tomorrow."), true);
  assert.equal(containsUnbookedConfirmation("Perfect—I'll book you for Monday at 1 PM and confirm shortly."), true);
  assert.equal(containsUnbookedConfirmation("You're welcome! I'll confirm the booking shortly."), true);
  assert.equal(containsUnbookedConfirmation('I have not scheduled anything.'), false);
});
