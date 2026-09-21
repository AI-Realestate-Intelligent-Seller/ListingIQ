# DealMachine integration

DealMachine is available to active platform administrators at `http://localhost:3000/operations/integrations/dealmachine` (with the existing `/platform-admin/integrations/dealmachine` route retained as an alias). Internal API routes use `/api/v1/integrations/dealmachine`. The browser never receives the API key or DealMachine authorization header, and there is no proxy or arbitrary-URL endpoint.

## Setup

1. Copy the values in `fastapi/dealmachine.env.example` into the backend environment. Use a random 32+ character `DEALMACHINE_ENCRYPTION_KEY` when storing the key through the settings endpoint.
2. Apply migrations: `cd fastapi && .venv311/bin/alembic upgrade head`.
3. Start the API: `.venv311/bin/uvicorn app.main:app --reload --port 8000`.
4. Start the UI: `cd ../frontend && npm run dev`.
5. Test Account, refresh Filters and Fields, map only filters returned by the live account, check Usage, then Count and Estimate before enabling paid operations.

The default base URL is fixed to `https://api.v2.dealmachine.com/v1`. A custom backend URL is rejected unless `DEALMACHINE_ALLOW_CUSTOM_BASE_URL=true`, which exists only for controlled provider mocks. No key is written to raw files or logs.

## Credit safety and workflow

Manual property searches and property-details requests force `contact_audience=none`. Estimates force `estimate_cost=true`. Paid search, property details, contact enrichment, export, list removal and forced re-enrichment require an explicit confirmation and audit reason. Usage is checked against provider balance plus total, property, people, per-run, per-day and reserve limits; additional credits are never purchased automatically.

Manual data flow: connection → usage → category mapping → count/estimate → limits → property search → immutable raw snapshot → `dm_property_id` deduplication → optional property details → separately approved contact enrichment.

There is no background scheduler or invented webhook. A developer can manually rerun a category and compare it with earlier snapshots; missing later matches mark membership inactive and properties are retained. Category searches are separate for OR behavior and merged by `dm_property_id`. Multiple filters inside one category use DealMachine AND logic.

Contacts are separate. Property search never queues or retrieves contacts. The operator must select saved `dm_property_id` values and call `/contacts/enrich`; `dm_person_id` identifies people, and all phones/emails and DNC flags are retained. Force Re-enrich requires confirmation and a reason.

Raw files are under `storage/integrations/dealmachine/{config,runs,raw,exports}`. Endpoint code uses the storage service path, allowing a future S3-compatible implementation without changing browser contracts.

## Internal route examples

Set `TOKEN` to a platform-admin access token. These calls target ListingIQ, never DealMachine directly.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/connection/test
curl -H "Authorization: Bearer $TOKEN" 'http://localhost:8000/api/v1/integrations/dealmachine/filters?refresh=true'
curl -H "Authorization: Bearer $TOKEN" 'http://localhost:8000/api/v1/integrations/dealmachine/fields?refresh=true'
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/usage
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"locations":[{"type":"state","code":"TX"}],"per_page":20}' http://localhost:8000/api/v1/integrations/dealmachine/properties/count
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"locations":[{"type":"state","code":"TX"}],"per_page":20}' http://localhost:8000/api/v1/integrations/dealmachine/properties/estimate
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"locations":[{"type":"state","code":"TX"}],"per_page":20,"confirmed":true,"reason":"Approved initial fetch"}' http://localhost:8000/api/v1/integrations/dealmachine/properties/search
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"dm_property_ids":["PROPERTY_ID"],"confirmed":true,"reason":"Approved details"}' http://localhost:8000/api/v1/integrations/dealmachine/properties/details
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"dm_property_ids":["PROPERTY_ID"],"contact_audience":"owners","confirmed":true,"reason":"Approved contacts"}' http://localhost:8000/api/v1/integrations/dealmachine/contacts/enrich
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"Saved properties","record_ids":["PROPERTY_ID"]}' http://localhost:8000/api/v1/integrations/dealmachine/lists
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/lists/LIST_ID
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"ids":["PROPERTY_ID"]}' http://localhost:8000/api/v1/integrations/dealmachine/lists/LIST_ID/items
curl -X DELETE -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"ids":["PROPERTY_ID"],"confirmed":true,"reason":"Remove invalid item"}' http://localhost:8000/api/v1/integrations/dealmachine/lists/LIST_ID/items
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"query":"equity"}' http://localhost:8000/api/v1/integrations/dealmachine/activity/search
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/activity/ACTIVITY_ID
curl -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"expected_count":20,"estimated_credits":20,"confirmed":true,"reason":"Approved export"}' http://localhost:8000/api/v1/integrations/dealmachine/properties/export
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/history
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/history/RUN_ID
curl -OJ -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/files/FILE_ID/download
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/settings
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/integrations/dealmachine/settings/test
```

`PUT /settings` and `PUT /categories/{key}` use the typed shapes shown in the UI request previews. Never paste a live key into terminal history; configure it through a protected secret manager or the authenticated settings form.

## Troubleshooting

- `403`: the user must be active with role `platform_admin`.
- `409`: confirmation, deduplication, concurrency, mapping, rate or credit safety blocked the operation. The response explains which guard fired.
- `429`: DealMachine rate limited a free/retryable request after bounded backoff. Paid calls are not automatically replayed.
- `502`: inspect the request ID in local execution history; provider response bodies and credentials are not logged.
- A category remains unavailable until Filters returns a suitable live filter and an administrator saves that mapping.
