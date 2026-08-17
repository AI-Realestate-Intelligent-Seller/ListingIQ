import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const client = fs.readFileSync(new URL('../client.js', import.meta.url), 'utf8');

test('new conversation form exposes all four required fields', () => {
  for (const id of ['contactName', 'contactPhone', 'propertyAddress', 'outreachReason']) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
  }
  assert.match(html, />\s*New conversation\s*<\/button>/);
  assert.match(html, /id="saveContactBtn"[^>]*>Start Conversation</);
});

test('create dialog no longer calls removed coaching code', () => {
  assert.doesNotMatch(client, /prepareCoachingSelect/);
});

test('single creation sends outreach reason to the outreach endpoint', () => {
  assert.match(client, /isNewConversation \? '\/outreach' : '\/conversations'/);
  assert.match(client, /outreach_reason: outreachReason/);
});
