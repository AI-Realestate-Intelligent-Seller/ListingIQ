# BatchData staged integration

Platform admins use `/platform-admin/integrations/batchdata`. Management routes live
under `/api/v1/integrations/batchdata` and require active platform-admin access.

## Workflow

1. **Property Search** takes category, location, combination and row settings. Save
   Configuration → Preview Endpoint → Run Endpoint. Confirmation requires a reason.
   Every request asks for the `basic`, `listing`, and `quicklist` datasets together.
   Records are deduplicated by provider identity and saved with their immutable
   search snapshots.
2. **Review Saved Properties** shows the same saved Property Search records. Reading
   the table and View property do not call BatchData.
3. **Contact Enrichment** independently selects saved Property Search records, then
   Preview Contacts → Get Contacts. Property Search never triggers enrichment.

The only BatchData provider endpoints used are `POST /api/v1/property/search` and
`POST /api/v1/property/skip-trace`. Search requests use `searchCriteria.query`, a
Quick List value, `options.take`/`skip`, and `datasets: ["basic", "listing",
"quicklist"]`. Contact requests use `requests[].propertyAddress`; each request
targets one selected saved property for explicit association. Internal routes are
`/products/property-search` and `/products/contact-enrichment`.

All selected stages accept `{property_ids, confirmed, reason, preview_hash}`. A local
preview returns a hash binding selection, mode, configuration version, address requests
and estimates. Confirmation must return that hash. The backend independently checks
saved discovery origin, current API mode, product access, previous stage attempts,
spending limits and confirmation. Selections support up to 10,000 unique local IDs;
each provider lookup targets one property. Get Details can start the local preview,
then requires a second click and confirmation to retrieve the selected records.

Detail pricing uses the configured full property lookup unit cost. Contact enrichment
also enforces its dedicated spend cap and monthly match allowance. Estimated spend is
reserved before provider calls. Failed/ambiguous requests remain blocked for the same
property and retain reservations for reconciliation; they are not blindly retried.
Raw requests/responses remain archived and immutable. Stage data is stored separately
in the property's operational copy, preserving its original discovery snapshot.

## Sandbox

Set `BATCHDATA_API_MODE=sandbox` and `BATCHDATA_API_TOKEN` to a provider-issued sandbox
token, then restart the backend. Use the normal workflow above; there is no separate
sandbox button. Sandbox properties use `batchdata_sandbox` and cannot enter production
retrieval stages. Actual recorded spend is zero; configured estimates/caps still apply.

Provider mocks may ignore filters or return different IDs/addresses from the selected
source. Sandbox stage results are explicitly marked sandbox and associated with the
requested source record; raw response identities remain preserved in the archive.
This is fixture association, not evidence that the provider matched the selected
address. Production detail responses require a matching property ID, and production
contact result inputs must match the selected address; mismatches require reconciliation.
Sandbox verification does not prove real account access, actual pricing, real filtering,
production delivery, or monitoring subscriptions.

## Saved data

Archives use local storage (`BATCHDATA_STORAGE_ROOT`) or private Cloudflare R2
(`BATCHDATA_STORAGE_BACKEND=r2`) without changing the database schema. The existing
`batchdata_saved_files.relative_path` column stores the archive key. R2 reads fall back
to local storage so files created before the switch remain available.

New property archives are append-only and use one folder per property:
`fetch-date=<UTC date>/user=<user id>/time=<UTC time>/session=<run id>/properties/`
`category=<primary category>/property=<provider property id>/`. Each property folder
contains its raw provider record, normalized record, search requests, metadata and any
later contact-enrichment request/response/result. Properties matching multiple categories
are stored once under their first matched category; `metadata.json` records every matched
category. Contact enrichment appends to the original property-search session folder.
If a later search returns the same provider property, its previously completed contact
result is reused and copied into the new property folder; the UI marks it already
enriched and does not offer another paid contact call.
Webhook archives remain partitioned by mode, receipt date and classification (`new`,
`updated`, `duplicate`, `failed`).
Each accepted webhook delivery saves a redacted payload plus metadata containing its
classification, provider property ID, processing status and payload hash. Existing raw
JSON database fields remain populated for now; R2 is the durable archive and no new
migration is required. Discovery saves per-property `property.json`, `normalized.json`,
`search_requests.json` and `metadata.json`. Contact enrichment adds
`contacts_request.json`, `contacts_response.json`, and `contacts.json` to that same
property folder. Data → Saved
Properties displays readable summary tables; View property includes the immutable
snapshot and cached detail/contact stage data. Activity → Provider Calls shows exact
requests/responses. History → Saved Files offers View and Download file. Lists paginate
25 records per page.

The authenticated APIs `/properties/{id}` and `/saved-files/{id}/content` provide
saved detail/file views. File access is limited to registered, safe archive keys.
Tokens remain server-side and absent from saved request archives.

## Monitoring

Monitoring pricing configuration and webhook handling are separate from this flow.
Subscription creation is not implemented. Test Connection only validates local setup.
Provider webhook delivery requires separately verified provider signing behavior and
public HTTPS configuration; sandbox searches do not verify that path.
