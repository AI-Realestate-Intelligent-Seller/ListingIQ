import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { analyticsSnapshot } from './db.js';
import { queueCounts } from './lead-queue.js';

const root = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.ANALYTICS_PORT || 5053);
const files = { '/': ['analytics/index.html', 'text/html'], '/app.js': ['analytics/app.js', 'application/javascript'], '/styles.css': ['analytics/styles.css', 'text/css'] };
http.createServer(async (req, res) => {
  if (req.url === '/api/dashboard') {
    try {
      const snapshot = analyticsSnapshot();
      const queue = await Promise.race([
        queueCounts(),
        new Promise(resolve => setTimeout(() => resolve({ unavailable: true, error: 'Redis timeout' }), 1200))
      ]).catch(error => ({ unavailable: true, error: error.message }));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ...snapshot, queue }));
    } catch (error) {
      res.writeHead(500, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: error.message }));
    }
    return;
  }
  const target = files[req.url];
  if (!target) { res.writeHead(404); res.end('Not found'); return; }
  fs.readFile(path.join(root, target[0]), (error, data) => {
    if (error) { res.writeHead(500); res.end(error.message); return; }
    res.writeHead(200, { 'Content-Type': target[1] }); res.end(data);
  });
}).listen(port, () => console.log(`Lead analytics dashboard: http://127.0.0.1:${port}`));
