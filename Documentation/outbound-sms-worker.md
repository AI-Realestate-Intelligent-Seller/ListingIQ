# Outbound SMS worker

Campaign Send validates and renders the complete locked recipient set, writes
one `outbound_sms_jobs` row per recipient, marks the campaign `sending`, and
returns immediately. Preview pagination is not an input to this operation.

Run a single Render background-worker service from the FastAPI directory:

```text
python -m app.sms.outbound_worker
```

Keep that service at exactly one instance. The process always starts ten SMS
submission threads. It uses the same `POSTGRES_URL`, `REDIS_URL`, Telnyx, and
application environment as the API. Apply Alembic revision
`0030_outbound_sms_jobs` before starting it.

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
