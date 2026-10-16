# Outbound SMS worker

Campaign Send validates and renders the complete locked recipient set, writes
one `outbound_sms_jobs` row per recipient, marks the campaign `sending`, and
returns immediately. Preview pagination is not an input to this operation.

The worker runs inside the existing FastAPI Render Web Service. Keep the
existing Uvicorn start command unchanged and set:

```text
SMS_WORKER_EMBEDDED_ENABLED=true
```

FastAPI startup creates a non-blocking supervisor thread. A Redis lease elects
one supervisor across Uvicorn processes and overlapping Render deploy
instances; only the lease owner starts the ten SMS submission threads. The
replacement instance waits until the prior instance stops and releases (or
loses) the lease. FastAPI shutdown signals the worker, joins all ten threads,
and releases the lease. No Render Background Worker or second server is
required.

Apply Alembic revision `0030_outbound_sms_jobs` before enabling the flag.

Redis assigns globally paced submission slots across every live SMS submission.
`SMS_MAX_SUBMISSIONS_PER_SECOND` is capped at 50. Set
`SMS_SENDER_SUBMISSIONS_PER_SECOND` to the lower approved sender/10DLC rate;
the smaller value wins. If Redis is unavailable, live SMS fails closed and
queued campaign work waits for retry.

Explicit HTTP 429 and 5xx responses retry with bounded exponential backoff.
Timeouts, connection loss during submission, successful non-JSON responses,
and workers that disappear while a request is in flight are marked
`uncertain` and are never automatically retried. This favors duplicate
prevention; an operator must reconcile uncertain jobs with Telnyx before any
manual resend.

Number Lookup remains a sequential coordinator stage in this worker. It is
intentionally not the concurrent lookup phase.
