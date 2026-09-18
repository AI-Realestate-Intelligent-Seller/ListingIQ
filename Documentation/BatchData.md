# BatchData staged integration

Platform admins use `/platform-admin/integrations/batchdata`. Management routes live
under `/api/v1/integrations/batchdata` and require active platform-admin access.

## Workflow

1. **Quick Lists** takes category, location, combination and row settings. Save
   Configuration → Preview Endpoint → Run Endpoint. Confirmation requires a reason.
   Only Quick Lists performs geographic/category discovery. Records are deduplicated
   by provider identity and saved with their immutable discovery snapshots.
2. **Review Saved Properties** opens Basic Property Data with the same saved Quick
   Lists records. Reading the table and View property do not call BatchData. Select
   properties using checkboxes, then Preview Details → Get Details. Selected local
   IDs, not new search filters or arbitrary addresses, drive this stage.
3. Listing information is included in **Basic Property Data**; there is no separate
   Listing Data tab or separate listing lookup charge.
   Pre-foreclosure properties are discovered through the Pre-Foreclosure category
   in Quick Lists; there is no separate Pre-Foreclosure tab.
4. **Contact Enrichment** independently selects saved Quick Lists properties, then
   Preview Contacts → Get Contacts. Discovery and details never trigger enrichment.

The provider's details endpoint is `POST /api/v1/property/lookup/all-attributes` with
`requests[].address` built from the immutable selected property's address. Contact
calls use `POST /api/v3/property/skip-trace` with `requests[].propertyAddress`.
Each request currently targets one selected property for explicit association.
Internal route names `/products/basic-property/search`, `/products/listing-data/search`,
and `/products/pre-foreclosure/search` are retained, but now require `property_ids`,
not category/location search fields. Contact enrichment uses `/products/contact-enrichment`.

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

Archives live under `storage/integrations/batchdata/<integration-id>/runs/<run-id>`
(or `BATCHDATA_STORAGE_ROOT`). Discovery saves per-call requests/responses plus
`properties/properties.json` and `properties/properties_csv.csv`. Selected stages save
per-source requests/responses plus consolidated `selected_results.json`. Data → Saved
Properties displays readable summary tables; View property includes the immutable
snapshot and cached detail/contact stage data. Activity → Provider Calls shows exact
requests/responses. History → Saved Files offers View and Download file. Lists paginate
25 records per page.

The authenticated APIs `/properties/{id}` and `/saved-files/{id}/content` provide
saved detail/file views. File access is limited to registered files within the archive
root. Tokens remain server-side and absent from saved request archives.

## Monitoring

Monitoring pricing configuration and webhook handling are separate from this flow.
Subscription creation is not implemented. Test Connection only validates local setup.
Provider webhook delivery requires separately verified provider signing behavior and
public HTTPS configuration; sandbox searches do not verify that path.
