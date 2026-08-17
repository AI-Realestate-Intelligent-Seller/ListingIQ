import path from 'path';
import { fileURLToPath } from 'url';
import Database from 'better-sqlite3';
import { Queue } from 'bullmq';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const confirmed = process.argv.includes('--yes');
if (!confirmed) {
  console.error('This permanently deletes every conversation, message, calendar booking, and queued lead.');
  console.error('Run: npm run reset:simulation -- --yes');
  process.exit(1);
}

if (typeof process.loadEnvFile === 'function') {
  try { process.loadEnvFile(path.join(root, '.env')); } catch {}
}

const chat = new Database(path.join(root, 'chat.db'));
const calendar = new Database(path.join(root, 'Simulation', 'calendar.db'));
const conversations = chat.prepare('SELECT COUNT(*) AS count FROM conversations').get().count;
const messages = chat.prepare('SELECT COUNT(*) AS count FROM messages').get().count;
const bookings = calendar.prepare("SELECT COUNT(*) AS count FROM sqlite_master WHERE type='table' AND name='bookings'").get().count
  ? calendar.prepare('SELECT COUNT(*) AS count FROM bookings').get().count
  : 0;

chat.transaction(() => {
  chat.prepare('DELETE FROM messages').run();
  chat.prepare('DELETE FROM conversations').run();
})();
calendar.transaction(() => {
  if (bookings) calendar.prepare('DELETE FROM bookings').run();
})();

chat.close();
calendar.close();

const redisUrl = new URL(process.env.REDIS_URL || 'redis://127.0.0.1:6379');
const queue = new Queue('lead-conversations', {
  connection: {
    host: redisUrl.hostname,
    port: Number(redisUrl.port || 6379),
    ...(redisUrl.password ? { password: redisUrl.password } : {}),
    connectTimeout: 1000,
    retryStrategy: () => null,
    maxRetriesPerRequest: 1,
    enableOfflineQueue: false
  }
});
queue.on('error', () => {});
let queueResult = 'not cleared';
try {
  queueResult = await Promise.race([
    queue.obliterate({ force: true }).then(() => 'cleared'),
    new Promise(resolve => setTimeout(() => resolve('unavailable (connection timeout)'), 2000))
  ]);
} catch (error) {
  queueResult = `unavailable (${error.message})`;
} finally {
  await queue.close().catch(() => queue.disconnect());
}

console.log(`Deleted ${conversations} conversations and ${messages} messages.`);
console.log(`Deleted ${bookings} calendar bookings.`);
console.log(`Redis lead queue: ${queueResult}.`);
