# Provider data and brokerage distribution

Operations → Integrations has Providers, Data and Distribution tabs. These are
platform-owner tools; all data endpoints require an active platform administrator.
Provider calls and messaging are never triggered by this workflow.

Data shows saved BatchData Quick Lists records, PropertyRadar properties and
DealMachine properties in separate, paginated tables. Combine Provider Data
creates or updates a platform-owned inventory. It preserves source references,
unions category memberships and combines saved contacts. Matching uses the
existing conservative address normalizer, retaining units, city and state while
ignoring a trailing ZIP. Incomplete addresses stay separate and cannot be allocated.
Provider records and raw snapshots are not modified. Combining again preserves
previous assignments and does not create duplicate inventory rows.

Lead statuses reflect contact readiness: ready, needs_review (no usable contact
or incomplete address), and dnc (all saved numbers restricted). Categories explain
why a property matched; listing status remains a separate field. Live properties
without phones can be distributed for review and stay needs_review in Lead Pool.

Distribution rules specify a brokerage, quantity, categories and lead statuses.
Properties must match both category and status filters; empty filters include all
values. Rules run in displayed order and each property
is allocated once. Preview shows allocations, shortages, exclusions and remaining
inventory. Confirmation requires the preview hash and a reason; changed inventory
or rules require another preview. Allocations, new leads and audit events commit
in one transaction. Conditional inventory claims and unique confirmation hashes
prevent repeated distribution. The same confirmation returns its saved result.

Live distribution creates leads under the brokerage's active head, or an active
broker when no head is available. Existing Lead Pool tenancy makes these leads
visible only to the receiving brokerage. Properties already present in any live
lead pool are excluded; previously distributed inventory is not allocated again.
Nothing assigns agents, starts campaigns or sends messages.

Sandbox/test mode is separate from live mode. BatchData sandbox properties and
PropertyRadar TEST- properties are test data; explicitly marked DealMachine test
properties are also isolated. Test distribution records allocations and history
but creates no tenant leads. The UI starts in sandbox mode for the current test
setup; choose Live data to prepare and distribute production records.

Schema: Alembic 0023_integration_distribution, after 0022_dealmachine. Startup's
existing create_all also creates the two new tables. API base:
/api/v1/platform-admin/integrations/data. Frontend pages:
/operations/integrations/data and /operations/integrations/distribution, with
platform-admin aliases. Distribution history is paginated and survives reloads.
