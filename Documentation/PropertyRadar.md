# PropertyRadar in the Internal Console

This integration extends the existing Next.js / FastAPI application. Only active `platform_admin` users can access management APIs. Customer roles remain unauthorized.

## Local URLs and commands

Verified development endpoints:

- Frontend: http://localhost:3000
- Integration directory: http://localhost:3000/platform-admin/integrations
- PropertyRadar: http://localhost:3000/platform-admin/integrations/propertyradar
- FastAPI health: http://localhost:8000/health
- Webhook: http://localhost:8000/api/v1/webhooks/propertyradar
- Management API prefix: `/api/v1/integrations/propertyradar`

PropertyRadar owns an explicit backend route for every operation. The main groups are
`/connection/test`, `/properties/*`, `/contacts/*`, `/lists/*`, `/monitoring/*`, `/events/*`, `/usage`,
`/api-logs`, and `/audit`. There are no generic `{action}` or `{resource}` proxy routes.
The shared `/api/v1/platform-admin/integrations` route only returns the provider-card
summary list.

The Data flow is deliberately split into three independently controlled calls:

- `POST /api/v1/integrations/propertyradar/properties/search` identifies and
  deduplicates RadarIDs with `Purchase=0`.
- `POST /api/v1/integrations/propertyradar/properties/details` accepts selected
  RadarIDs, requires confirmation and a reason, purchases details, and saves them.
- `POST /api/v1/integrations/propertyradar/contacts/enrich` accepts only saved
  RadarIDs and separately enforces phone/email allowance checks.

Property details never automatically trigger contact enrichment through this manual
flow.

The existing console lives at `/platform-admin`, so this integration follows that convention instead of introducing `/internal-console`.

Run in separate terminals from the repository root:

```bash
cd project/frontend
npm run dev -- --port 3000
```

```bash
cd project/fastapi
.venv311/bin/python -m alembic upgrade head
.venv311/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd project/fastapi
.venv311/bin/python -m app.propertyradar.worker
```

Run the worker as a separate supervised service in deployment. Incoming deliveries and skip-tracing jobs are database-backed. Restarting the API does not discard queued events. No FastAPI `BackgroundTasks` are used. Multiple workers share a committed database compare-and-set operation lock. Do not start another API application for this integration.

## Environment

Backend `.env` (never a `NEXT_PUBLIC_` variable):

```dotenv
PROPERTYRADAR_API_TOKEN=
PROPERTYRADAR_API_BASE_URL=https://api.propertyradar.com
PROPERTYRADAR_PUBLIC_WEBHOOK_URL=
PROPERTYRADAR_WEBHOOK_SECRET=
```

The API token is environment-only and is never stored in the database or returned to the browser. The webhook secret must be a cryptographically random string of at least 32 characters. Generate it securely, for example with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`, and save it in the backend environment. A local secret was generated during implementation; its value was not displayed.

`PROPERTYRADAR_PUBLIC_WEBHOOK_URL`, when set, overrides the URL stored in Configuration. Use the exact public HTTPS route ending `/api/v1/webhooks/propertyradar`. Restart the API and worker after changing environment secrets. Secret rotation requires coordinating the provider's stored webhook secret; never just change one side.

`FRONTEND_URL` determines whether the page displays **Local development mode**. The normal repository default is `http://localhost:3000`. External webhook registration is blocked without a public HTTPS URL. A tunnel is optional and must be configured by the operator; the integration never installs or starts one.

## First use

1. Configure the token and webhook secret in the backend environment.
2. Open Configuration, test the connection, set geography and the actual PropertyRadar billing-cycle start date, and enable local processing.
3. Select categories (one, several, or all 21) under Search Settings. The default is 20 RadarIDs **per category**, with an application maximum of 100 per category.
4. Run Property Search. This free call returns deduplicated RadarIDs and does not purchase details or contacts.
5. Paste selected RadarIDs into Property Details and confirm the purchase. Existing properties are not repurchased. Contact enrichment remains a separate action accepting only saved RadarIDs.
6. Prepare dynamic lists, validate their complete populations, and confirm monitoring. This does not limit lists to the initially imported rows.
7. Configure a public HTTPS webhook URL and register/update webhook automations. Existing automation settings and webhook destinations are merged; `PurchasePhoneOptions` and `PurchaseEmailOptions` are never sent.
8. Keep the worker running to process events and skip-tracing jobs. Deferred events and jobs can be retried from the console after allowance or configuration issues are resolved.

Lists with changed criteria are retained; Prepare Lists creates a new list for the changed criteria. Current-criteria lists plus every already-monitored list are included in conservative monitoring validation. Retired, paused lists remain visible but are excluded. Pause an old list before replacing its geography when its old population exceeds a limit. List creation and webhook registration are not blindly retried after an ambiguous provider failure; reconcile their IDs before proceeding.

## Identity, inventory, and snapshots

The existing `Lead` is a tenant-owned, single-phone outreach prospect. It is unsuitable for a platform-owned property that can have several owners, phones, emails, and categories. `provider_properties` therefore holds the mutable platform property inventory. It does not create tenant leads or assign contacts to campaigns.

- One globally unique `radar_id` is one property.
- `property_category_memberships` preserves every category relationship.
- Phone/email uniqueness is scoped to a property, never global.
- Completed owner unlocks are reused across properties sharing a PersonKey; the second property remains a distinct property.
- `property_provider_snapshots` is append-only in application processing, with hash and UTC timestamps. ORM updates/deletes are rejected.
- Raw webhook deliveries, including retries and invalid authenticated JSON, are retained. Authentication secrets accidentally echoed into payloads are redacted before persistence.
- Change events retain old/new values in the mutable copy's change history and preserve the original event snapshot. Unknown Change/New Record properties remain in event history without a details purchase.
- Initial skip tracing defaults off. Genuine new webhook properties default on. Existing RadarIDs, Change events, and New Record events never automatically queue another skip trace.
- Existing DNC/TCPA and campaign authorization remain unchanged. Provider contacts do not bypass outreach validation.

## Budget and concurrency behavior

Default hard caps are 2,450 phone unlocks, 2,450 email unlocks, 45,000 property exports, 45,000 unique monitored properties, and 10,000 properties per list. The UI and backend reject attempts to raise them above these ceilings.

Each details batch contains at most 500 IDs. Identification and preview requests use `Purchase=0`. Actual details imports use individually keyed RadarID purchases, allowing interrupted imports to resume without repurchasing previously completed properties. All identification searches finish and RadarIDs are deduplicated before the initial details preview.

A purchase is blocked if required quote metadata is absent, cost is nonzero, provider free quantity is insufficient, or local usage plus the reservation exceeds the cap. Allowances are checked under the integration's cross-process lock. A reservation is committed **before** `Purchase=1`; an uncertain response continues consuming the reserved allowance. Purchase calls have no transport retries.

The billing anchor is locked after the first reservation because changing it could reset local usage. Billing cycle end dates are exclusive, and shorter months preserve the original anchor day.

The worker revalidates active list populations every five minutes while local processing is enabled and pauses provider monitoring when it detects an unsafe population. Pausing local processing stops API and skip-tracing jobs, but **does not pause provider monitoring**. Incoming events remain pending. Use Pause Monitoring separately.

Provider limitation: quotes and purchases are separate API requests. ListingIQ serializes its own operations, but cannot atomically reserve PropertyRadar account allowances against activity in other applications or guarantee that a dynamic population will not grow between checks. Use a dedicated provider account/allocation and provider-side billing controls. If a purchase returns a nonzero cost after a zero-cost quote, ListingIQ pauses further processing and records the incident; it cannot reverse that provider charge. Live account behavior requires verification with the actual Business-plan account before enabling production processing.

## Crash / uncertain-operation recovery

The operation lock intentionally does not expire automatically. A process killed during an external purchase must not allow another worker to issue the same purchase again. The console reports the busy condition; the usage ledger exposes `reserved` or `uncertain` operations.

Recovery is an operator task:

1. Stop the failed worker/operation and pause local processing.
2. Inspect usage records, endpoint, RadarID/PersonKey, provider request IDs, and the provider account history.
3. Reconcile the provider outcome. Preserve reservations for unknown outcomes; never delete them to force a retry. A completed reservation can reuse its saved response. If provider support confirms a request never executed, an operator may resolve that record under a controlled database transaction.
4. Only after confirming the old process cannot resume, clear `integration_configs.lock_token` and `lock_started_at` for `provider='propertyradar'` in an operator transaction.
5. Resume the worker and retry the affected event/job/import. All budget and deduplication checks still apply.

Ambiguous list creation (`status=error` with no provider ID) and webhook registration (`webhook_id=registration_pending`) similarly require matching the actual provider object and saving its ID. There is deliberately no blind retry button for uncertain purchases or external object creation.

## Local webhook test

Use the actual local webhook secret from your backend environment, never the PropertyRadar API token:

```bash
curl --request POST \
  "http://localhost:8000/api/v1/webhooks/propertyradar" \
  --header "Authorization: Bearer YOUR_LOCAL_WEBHOOK_SECRET" \
  --header "Content-Type: application/json" \
  --data '{
    "RadarID": "TEST-PROPERTY-001",
    "ListName": "ListingIQ - Chicago - Expired",
    "TriggerType": "New Match",
    "Change1": "Listing Status:Active:Expired"
  }'
```

Expected: HTTP 202, `status: "test"`, and an event row visible under Webhooks and Events. Reserved `TEST-` RadarIDs can never reach PropertyRadar, even if sent to a production instance. The page's **Copy test curl** button copies a placeholder secret, never the configured secret.

A repeatable local smoke script reads the secret without printing it, runs curl, verifies the durable row, and checks authenticated event visibility using an existing active platform administrator:

```bash
cd project/fastapi
.venv311/bin/python scripts/propertyradar_local_smoke.py
```

## Verification commands

```bash
cd project/fastapi
.venv311/bin/python -m pip install -r requirements-dev.txt
.venv311/bin/python -m ruff check app/propertyradar app/routes/propertyradar.py tests/propertyradar alembic/versions/0020_propertyradar.py
.venv311/bin/python -m ruff format --check app/propertyradar app/routes/propertyradar.py tests/propertyradar alembic/versions/0020_propertyradar.py
.venv311/bin/python -m pytest --confcutdir=tests/propertyradar tests/propertyradar -q
.venv311/bin/python -m pytest tests -q
```

```bash
cd project/frontend
npm test
npm run typecheck
npm run format:integrations
npm run lint
npm run build -- --webpack
```

Migration: `0020_propertyradar`, following `0019_platform_admin`. It supports tables already created by the existing development startup `create_all` convention, and rejects incomplete existing schemas. Upgrade/downgrade/upgrade was tested on a SQLite backup before applying the upgrade locally. Do not downgrade a populated production integration: downgrade removes its inventory and history.

## Provider references and mapping assumptions

Criteria were checked against https://developers.propertyradar.com/criteria_reference and the OpenAPI document linked at https://developers.propertyradar.com/api on 2026-09-09. The centralized mapping lives in `app/propertyradar/categories.py`.

Cash Buyer uses the actual search criterion `isCashTransaction`, representing a market transfer without simultaneous financing. No unrequested recency window is applied. High Equity uses `EquityPercent`; Vacant Land uses nested `PropertyType` / `PType=LND`; FIPS and ZIP filters use `FIPS` and `ZipFive`. These are search criteria, not guessed response-field aliases.

External verification still requires a real API token, confirmed billing-cycle anchor and account allowances, a public HTTPS webhook URL, and provider deliveries. Automated tests use a fake provider and never purchase real records.

## File inventory

Created:

- `fastapi/app/propertyradar/{__init__,models,categories,config,client,service,worker}.py`
- `fastapi/app/routes/propertyradar.py`
- `fastapi/alembic/versions/0020_propertyradar.py`
- `fastapi/tests/propertyradar/{conftest,test_integration}.py`
- `fastapi/scripts/propertyradar_local_smoke.py`
- `fastapi/requirements-dev.txt`
- `fastapi/propertyradar.env.example`
- `frontend/src/app/platform-admin/integrations/page.tsx`
- `frontend/src/app/platform-admin/integrations/propertyradar/page.tsx`
- `frontend/src/features/integrations/{types.ts,propertyradar-view.tsx,propertyradar-view.test.tsx}`
- `frontend/vitest.config.ts`, `frontend/eslint.config.mjs`
- This document.

Modified:

- `fastapi/app/main.py` (register routes and seed integration metadata at startup)
- `frontend/src/features/platform-admin/components/platform-admin-view.tsx` (Operations navigation and reuse of console shell)
- `frontend/src/lib/api/http-client.ts` (support PUT)
- `frontend/src/app/globals.css` (integration styles using existing console classes)
- `frontend/package.json`, `frontend/package-lock.json` (test/format tooling)
- Local ignored `fastapi/.env` (generated webhook secret) and `fastapi/listingiq.db` (migration and clearly marked test deliveries).

The Git repository is rooted at `project/`. The final diff was reviewed there. An existing change in `frontend/src/features/auth/components/auth-visual.tsx` is outside this integration and was left untouched. Customer authentication, leads, campaigns and messaging implementations were not edited.
