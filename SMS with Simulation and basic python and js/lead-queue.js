import { Queue, Worker } from 'bullmq';

const redisUrl = new URL(process.env.REDIS_URL || 'redis://127.0.0.1:6379');
const connection = {
  host: redisUrl.hostname,
  port: Number(redisUrl.port || 6379),
  ...(redisUrl.password ? { password: redisUrl.password } : {}),
  maxRetriesPerRequest: null
};
const queue = new Queue('lead-conversations', { connection });
queue.on('error', error => console.error(`[lead-queue] Redis error: ${error.message}`));

async function enqueueLead(lead) {
  return queue.add('run-conversation', lead, {
    jobId: `lead-${lead.contact.replace(/\D/g, '')}-${Date.now()}`,
    removeOnComplete: true,
    removeOnFail: 500
  });
}

function startLeadWorker(processor) {
  const worker = new Worker('lead-conversations', processor, { connection, concurrency: 1 });
  worker.on('active', job => console.log(`[lead-queue] started ${job.data.contact}`));
  worker.on('completed', job => console.log(`[lead-queue] completed ${job.data.contact}`));
  worker.on('failed', (job, error) => console.error(`[lead-queue] failed ${job?.data?.contact}: ${error.message}`));
  worker.on('error', error => console.error(`[lead-queue] worker error: ${error.message}`));
  return worker;
}

async function queueCounts() {
  return queue.getJobCounts('waiting', 'active', 'completed', 'failed', 'delayed');
}

export { enqueueLead, startLeadWorker, queueCounts };
