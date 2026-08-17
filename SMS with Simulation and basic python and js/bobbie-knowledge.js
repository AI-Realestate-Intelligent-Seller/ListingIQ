const DEFAULT_RAG_URL = 'http://127.0.0.1:5051/rag/search';

async function searchBobbieKnowledge(query, limit = 4) {
  const ragUrl = process.env.BOBBIE_RAG_URL || DEFAULT_RAG_URL;
  const response = await fetch(ragUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query: String(query || '').slice(0, 500), limit }),
    signal: AbortSignal.timeout(10000)
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Bobbie knowledge service returned HTTP ${response.status}`);
  }
  return payload;
}

export { searchBobbieKnowledge };
