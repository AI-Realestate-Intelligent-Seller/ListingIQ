import path from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const DB_PATH = path.join(__dirname, 'chat.db');

const db = new Database(DB_PATH);

db.exec(`
CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY,
  contact TEXT UNIQUE,
  name TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER,
  direction TEXT,
  from_number TEXT,
  to_number TEXT,
  text TEXT,
  status TEXT,
  event_type TEXT,
  telnyx_id TEXT,
  created_at TEXT,
  FOREIGN KEY(conversation_id) REFERENCES conversations(id)
);
`);

const conversationColumns = db.prepare('PRAGMA table_info(conversations)').all().map(column => column.name);
if (!conversationColumns.includes('property_address')) {
  db.exec('ALTER TABLE conversations ADD COLUMN property_address TEXT');
}
if (!conversationColumns.includes('ai_enabled')) {
  db.exec('ALTER TABLE conversations ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 0');
}
if (!conversationColumns.includes('simulation_intent_case')) {
  db.exec('ALTER TABLE conversations ADD COLUMN simulation_intent_case TEXT');
}
if (!conversationColumns.includes('simulation_intent_definition')) {
  db.exec('ALTER TABLE conversations ADD COLUMN simulation_intent_definition TEXT');
}
if (!conversationColumns.includes('recipient_ai_enabled')) {
  db.exec('ALTER TABLE conversations ADD COLUMN recipient_ai_enabled INTEGER NOT NULL DEFAULT 1');
}
if (!conversationColumns.includes('lead_context')) {
  db.exec('ALTER TABLE conversations ADD COLUMN lead_context TEXT');
}
for (const [column, definition] of [
  ['queue_status', "TEXT NOT NULL DEFAULT 'idle'"],
  ['lead_status', "TEXT NOT NULL DEFAULT 'processing'"],
  ['dnc_alert', 'INTEGER NOT NULL DEFAULT 0'],
  ['meeting_booked', 'INTEGER NOT NULL DEFAULT 0'],
  ['processed_at', 'TEXT']
]) {
  if (!conversationColumns.includes(column)) db.exec(`ALTER TABLE conversations ADD COLUMN ${column} ${definition}`);
}

function getOrCreateConversation(contact, name = null) {
  const row = db.prepare('SELECT id, contact, name, property_address, ai_enabled, recipient_ai_enabled, lead_context FROM conversations WHERE contact = ?').get(contact);
  if (row) return row;
  const now = new Date().toISOString();
  const info = db.prepare('INSERT INTO conversations(contact, name, created_at) VALUES(?,?,?)').run(contact, name, now);
  return { id: info.lastInsertRowid, contact, name, ai_enabled: 0, recipient_ai_enabled: 1 };
}

function setConversationName(contact, name) {
  db.prepare('UPDATE conversations SET name = ? WHERE contact = ?').run(name, contact);
}

function updateConversationSettings(contact, settings = {}) {
  const current = db.prepare('SELECT name, property_address, ai_enabled, recipient_ai_enabled, lead_context FROM conversations WHERE contact = ?').get(contact);
  if (!current) return 0;
  const name = settings.name !== undefined ? settings.name : current.name;
  const propertyAddress = settings.property_address !== undefined ? settings.property_address : current.property_address;
  const aiEnabled = settings.ai_enabled !== undefined ? (settings.ai_enabled ? 1 : 0) : current.ai_enabled;
  const recipientAiEnabled = settings.recipient_ai_enabled !== undefined
    ? (settings.recipient_ai_enabled ? 1 : 0)
    : current.recipient_ai_enabled;
  const leadContext = settings.lead_context !== undefined ? settings.lead_context : current.lead_context;
  return db.prepare('UPDATE conversations SET name = ?, property_address = ?, ai_enabled = ?, recipient_ai_enabled = ?, lead_context = ? WHERE contact = ?')
    .run(name || null, propertyAddress || null, aiEnabled, recipientAiEnabled, leadContext || null, contact).changes;
}

function getConversation(contact) {
  return db.prepare('SELECT id, contact, name, property_address, ai_enabled, recipient_ai_enabled, lead_context, queue_status, lead_status, dnc_alert, meeting_booked, processed_at, created_at FROM conversations WHERE contact = ?').get(contact);
}

function addMessage(conversation_id, props) {
  const now = new Date().toISOString();
  const info = db.prepare(
    `INSERT INTO messages(conversation_id, direction, from_number, to_number, text, status, event_type, telnyx_id, created_at)
     VALUES(?,?,?,?,?,?,?,?,?)`
  ).run(
    conversation_id,
    props.direction || null,
    props.from_number || null,
    props.to_number || null,
    props.text || null,
    props.status || null,
    props.event_type || null,
    props.telnyx_id || null,
    now
  );
  return info.lastInsertRowid;
}

function listConversations() {
  return db.prepare(
    `SELECT c.contact, c.name, c.property_address, c.ai_enabled, c.recipient_ai_enabled, c.created_at, m.text as last_text, m.event_type as last_event_type, m.direction as last_direction, m.created_at as last_at
     FROM conversations c
     LEFT JOIN messages m ON m.id = (
       SELECT id FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1
     )
     ORDER BY last_at DESC NULLS LAST, c.created_at DESC`
  ).all();
}

function getMessages(contact) {
  const conv = db.prepare('SELECT id FROM conversations WHERE contact = ?').get(contact);
  if (!conv) return [];
  return db.prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC').all(conv.id);
}

function findMessageByTelnyxId(telnyx_id) {
  if (!telnyx_id) return null;
  return db.prepare('SELECT * FROM messages WHERE telnyx_id = ?').get(telnyx_id);
}

function updateMessageStatusByTelnyxId(telnyx_id, status, event_type) {
  if (!telnyx_id) return null;
  const now = new Date().toISOString();
  const info = db.prepare('UPDATE messages SET status = ?, event_type = ?, created_at = ? WHERE telnyx_id = ?').run(status, event_type, now, telnyx_id);
  return info.changes;
}

function deleteMessage(id) {
  return db.prepare('DELETE FROM messages WHERE id = ?').run(id).changes;
}

const deleteConversationTransaction = db.transaction(contact => {
  const conversation = db.prepare('SELECT id FROM conversations WHERE contact = ?').get(contact);
  if (!conversation) return 0;
  db.prepare('DELETE FROM messages WHERE conversation_id = ?').run(conversation.id);
  return db.prepare('DELETE FROM conversations WHERE id = ?').run(conversation.id).changes;
});

function deleteConversation(contact) {
  return deleteConversationTransaction(contact);
}

function updateLeadProgress(contact, values = {}) {
  const current = db.prepare('SELECT queue_status,lead_status,dnc_alert,meeting_booked,processed_at FROM conversations WHERE contact = ?').get(contact);
  if (!current) return 0;
  return db.prepare('UPDATE conversations SET queue_status=?,lead_status=?,dnc_alert=?,meeting_booked=?,processed_at=? WHERE contact=?').run(
    values.queue_status ?? current.queue_status,
    values.lead_status ?? current.lead_status,
    values.dnc_alert !== undefined ? (values.dnc_alert ? 1 : 0) : current.dnc_alert,
    values.meeting_booked !== undefined ? (values.meeting_booked ? 1 : 0) : current.meeting_booked,
    values.processed_at !== undefined ? values.processed_at : current.processed_at,
    contact
  ).changes;
}

function analyticsSnapshot() {
  const categories = db.prepare('SELECT lead_status AS category, COUNT(*) AS count FROM conversations GROUP BY lead_status').all();
  const totals = db.prepare(`SELECT COUNT(*) AS total, SUM(CASE WHEN processed_at IS NOT NULL THEN 1 ELSE 0 END) AS processed,
    SUM(meeting_booked) AS meetings, SUM(dnc_alert) AS dnc FROM conversations`).get();
  const leads = db.prepare(`SELECT contact,name,property_address,queue_status,lead_status,dnc_alert,meeting_booked,processed_at,created_at
    FROM conversations ORDER BY dnc_alert DESC, meeting_booked DESC, created_at DESC`).all();
  return { categories, totals: { total: totals.total || 0, processed: totals.processed || 0, meetings: totals.meetings || 0, dnc: totals.dnc || 0 }, leads };
}

export { getOrCreateConversation, getConversation, addMessage, listConversations, getMessages, setConversationName, updateConversationSettings, findMessageByTelnyxId, updateMessageStatusByTelnyxId, deleteMessage, deleteConversation, updateLeadProgress, analyticsSnapshot };
