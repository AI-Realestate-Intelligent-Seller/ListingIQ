import test from 'node:test';
import assert from 'node:assert/strict';
import { buildInitialOutreach, buildSingleLeadContext } from '../outreach.js';

test('single conversation stores the same core lead context as bulk outreach', () => {
  const context = buildSingleLeadContext('123 Main St, Chicago, IL', 'The listing expired without a recorded sale.');
  assert.equal(context.property_address, '123 Main St, Chicago, IL');
  assert.equal(context.outreach_reason, 'The listing expired without a recorded sale.');
  assert.equal(context.lead_source, 'Manual single conversation');
  assert.equal(context.phone_number_source.available, false);
  assert.deepEqual(context.property_details, {});
});

test('single conversation introduction uses name, address, and outreach reason', () => {
  const message = buildInitialOutreach(
    'Maya Chen',
    '123 Main St, Chicago, IL',
    'The listing expired without a recorded sale.'
  );
  assert.equal(message, 'Hey Maya, I’m reaching out about 123 Main St, Chicago, IL. The listing expired without a recorded sale. Would you be open to a brief conversation? Bobbie Fisher – RE/MAX');
});
