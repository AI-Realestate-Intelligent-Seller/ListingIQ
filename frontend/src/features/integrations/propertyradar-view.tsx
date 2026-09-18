"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { requestJson } from "@/lib/api/http-client";
import { publicEnv } from "@/config/public-env";
import {
  ConfirmDialog,
  type ConfirmRequest,
} from "@/components/dialog/confirm-dialog";
import { Toast } from "@/components/toast/toast";
import {
  ProviderIcon,
  ProviderIntegrationCard,
} from "./provider-integration-card";
import {
  IntegrationNavigation,
  type IntegrationSection,
  type IntegrationSubtab,
} from "./integration-navigation";
import { IntegrationActionGuidance } from "./integration-cost";
import type { Config, MonitoringPreview, Records, Summary } from "./types";

const root = "/integrations/propertyradar";
const tabs = [
  "Connection",
  "Configuration",
  "Property Search",
  "Property Details",
  "Contact Enrichment",
  "Leads",
  "Leads by Category",
  "Leads by Run",
  "Search Settings",
  "Lead Lists",
  "Webhooks & Events",
  "Limits & Usage",
  "API Logs",
] as const;
type PropertyRadarTab = (typeof tabs)[number];
const propertyRadarSections: Record<
  IntegrationSection,
  readonly IntegrationSubtab[]
> = {
  Connection: [{ id: "Connection", label: "Connection" }],
  Configuration: [{ id: "Configuration", label: "Configuration" }],
  Data: [
    { id: "Property Search", label: "Property Search" },
    { id: "Property Details", label: "Property Details" },
    { id: "Contact Enrichment", label: "Contact Enrichment" },
    { id: "Leads", label: "Properties" },
    { id: "Leads by Category", label: "Categories" },
    { id: "Leads by Run", label: "Runs" },
    { id: "Search Settings", label: "Search Settings" },
    { id: "Lead Lists", label: "Lists" },
  ],
  "Usage & Limits": [{ id: "Limits & Usage", label: "Usage & Limits" }],
  Activity: [{ id: "Webhooks & Events", label: "Webhooks & Events" }],
  History: [{ id: "API Logs", label: "Audit & API Logs" }],
};
const propertyRadarSectionFor = (tab: PropertyRadarTab) =>
  (Object.entries(propertyRadarSections).find(([, subtabs]) =>
    subtabs.some((subtab) => subtab.id === tab),
  )?.[0] || "Connection") as IntegrationSection;
const tabHelp: Record<(typeof tabs)[number], string> = {
  Connection:
    "Setup checklist: what's read from the server's .env, and whether PropertyRadar is reachable.",
  Configuration:
    "Where PropertyRadar looks for leads, and what happens automatically when it finds them.",
  "Property Search":
    "Run only the free PropertyRadar identification search and return deduplicated RadarIDs.",
  "Property Details":
    "Purchase and save details only for selected RadarIDs after explicit confirmation.",
  "Contact Enrichment":
    "Unlock owner phone and email data only for already-saved, deduplicated properties.",
  Leads:
    "Total property volume, and how much of it is new or updated recently.",
  "Leads by Category":
    "One row per category: the dynamic list PropertyRadar keeps in sync and how many properties it currently matches.",
  "Leads by Run":
    "Every individual property purchase or reservation, in the order it happened.",
  "Search Settings":
    "Choose the categories and geography used by the separate Property Search endpoint.",
  "Lead Lists": "Manage prepared provider lists and their monitoring state.",
  "Webhooks & Events":
    "The delivery endpoint PropertyRadar calls when a monitored lead changes, and the events received on it.",
  "Limits & Usage":
    "How much of each monthly allowance has been used, what's left, and the skip-tracing jobs spending it.",
  "API Logs":
    "Full audit history: every admin action and every billable provider call, from day one.",
};
// Friendly headers for raw column keys. Anything not listed here falls back
// to a plain word-spaced version of the key.
const columnLabels: Record<string, string> = {
  category: "Category",
  provider_list_id: "Provider list ID",
  list_name: "List name",
  total_count: "Properties matched",
  is_monitored: "Monitored",
  automation_status: "Automation",
  last_synced_at: "Last synced",
  error_message: "Error",
  received_at: "Received",
  radar_id: "Radar ID",
  trigger_type: "Trigger",
  is_test: "Test event",
  is_duplicate_property: "Duplicate property",
  is_retry_duplicate: "Retry duplicate",
  change_1: "Change 1",
  change_2: "Change 2",
  change_3: "Change 3",
  processing_status: "Processing",
  skiptrace_status: "Skip trace",
  raw_payload: "Payload",
  status: "Status",
  selected_contact_mode: "Contact mode",
  queued_at: "Queued",
  deferred_until: "Deferred until",
  created_at: "Created",
  billing_cycle: "Billing cycle",
  usage_type: "Usage type",
  person_key: "Person key",
  preview: "Preview",
  purchased: "Purchased",
  result_count: "Results",
  quantity_free_remaining: "Free remaining",
  total_cost: "Cost",
  endpoint: "Endpoint",
  request_id: "Request ID",
  actor_email: "By",
  action: "Action",
  target_type: "Target type",
  target_id: "Target ID",
  detail: "Detail",
};
const label = (value: string) =>
  columnLabels[value] ?? value.replaceAll("_", " ");
const date = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
// A small "i" badge that reveals help text on hover/focus instead of taking
// up permanent space with a paragraph.
function InfoTip({ text }: { text: string }) {
  return (
    <span className="pr-infotip" tabIndex={0}>
      <span aria-hidden="true">i</span>
      <span className="pr-infotip-bubble" role="tooltip">
        {text}
      </span>
      <span className="sr-only">{text}</span>
    </span>
  );
}

async function api<T>(
  path = "",
  method: "GET" | "POST" | "PUT" = "GET",
  payload?: unknown,
): Promise<T> {
  const session = readAuthSession();
  if (!session || session.user.role !== "platform_admin")
    throw new Error("Internal administrator access is required.");
  return requestJson<T>(root + path, {
    method,
    payload,
    accessToken: session.access_token,
  });
}

export function PropertyRadarView({ detail = false }: { detail?: boolean }) {
  const [data, setData] = useState<Summary | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [tab, setTab] = useState<PropertyRadarTab>(tabs[0]);
  const [busy, setBusy] = useState("");
  const busyRef = useRef(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [monitoring, setMonitoring] = useState<MonitoringPreview | null>(null);
  const [confirmation, setConfirmation] = useState<ConfirmRequest | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const reload = useCallback(async (reset = false) => {
    const value = await api<Summary>();
    setData(value);
    setDraft((current) => (reset || !current ? value.config : current));
    setRefreshKey((current) => current + 1);
  }, []);
  useEffect(() => {
    void reload().catch((reason) => setError(reason.message));
  }, [reload]);
  async function work(name: string, action: () => Promise<void>) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(name);
    setError("");
    setNotice("");
    try {
      await action();
      await reload();
      setNotice(`${name} completed.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Operation failed.");
    } finally {
      busyRef.current = false;
      setBusy("");
    }
  }
  const dirty =
    !!data && !!draft && JSON.stringify(data.config) !== JSON.stringify(draft);
  const blocked = !!busy || !!data?.busy;
  async function run(name: string, endpoint: string, body: unknown = {}) {
    await work(name, async () => {
      await api(endpoint, "POST", body);
    });
  }
  function change<K extends keyof Config>(key: K, value: Config[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setMonitoring(null);
  }
  function toggle() {
    if (!data) return;
    void work(
      data.enabled ? "Pause local processing" : "Resume local processing",
      async () => {
        const value = await api<Summary>("/config", "PUT", {
          ...data.config,
          enabled: !data.enabled,
        });
        setData(value);
        setDraft((current) =>
          current ? { ...current, enabled: value.enabled } : value.config,
        );
      },
    );
  }
  function save(event: React.FormEvent) {
    event.preventDefault();
    void work("Save configuration", async () => {
      await api<Summary>("/config", "PUT", {
        ...draft,
        zip_codes: draft?.zip_codes.map((zip) => zip.trim()).filter(Boolean),
      });
      await reload(true);
      setMonitoring(null);
    });
  }
  const button = (name: string, endpoint: string, disabled = false) => (
    <button
      type="button"
      className="button secondary"
      disabled={blocked || disabled}
      onClick={() => void run(name, endpoint)}
    >
      {busy === name ? "Working…" : name}
    </button>
  );
  if (!data || !draft)
    return (
      <section className="pr-workspace" aria-live="polite">
        {error ? (
          <p className="platform-error" role="alert">
            {error}
          </p>
        ) : (
          <p>Loading PropertyRadar…</p>
        )}
        <button
          className="button secondary"
          onClick={() =>
            void reload().catch((reason) => setError(reason.message))
          }
        >
          Refresh
        </button>
      </section>
    );

  const integrationStatus =
    data.status === "Error"
      ? "Error"
      : data.connection_status === "connected"
        ? "Ready"
        : data.token_configured
          ? "Needs testing"
          : "Needs setup";
  const integrationStatusTone =
    integrationStatus === "Ready"
      ? "on"
      : integrationStatus === "Error"
        ? "off"
        : "neutral";

  return (
    <div className="pr-workspace">
      {error && (
        <Toast message={error} tone="error" onDone={() => setError("")} />
      )}
      {notice && (
        <Toast message={notice} tone="success" onDone={() => setNotice("")} />
      )}
      {busy && <p role="status">{busy}…</p>}
      {data.busy && (
        <p className="platform-notice">
          An integration operation is in progress. Refresh to see its result. A
          stopped worker may require operator reconciliation.
        </p>
      )}
      {!detail ? (
        <ProviderIntegrationCard
          provider="propertyradar"
          name="PropertyRadar"
          href="/platform-admin/integrations/propertyradar"
          status={integrationStatus}
          statusTone={integrationStatusTone}
          description="Property intelligence, monitored lists, webhooks and owner contact enrichment."
          metrics={[
            {
              label: "Connection",
              value: data.connection_status.replaceAll("_", " "),
            },
            {
              label: "Credential",
              value: data.token_configured ? "Configured" : "Not configured",
            },
            {
              label: "Contact usage",
              value: `${data.usage.used.phone_unlock + data.usage.used.email_unlock} unlocks`,
            },
            {
              label: "Monitored lists",
              value: data.metrics.active_monitored_lists,
            },
            {
              label: "Last activity",
              value: date(data.last_successful_sync_at),
            },
            { label: "Last error", value: data.last_error || "None" },
          ]}
          notice={
            !data.token_configured || !data.webhook_secret_configured ? (
              <>
                Needs setup:{" "}
                {[
                  !data.token_configured && "API token",
                  !data.webhook_secret_configured && "webhook secret",
                ]
                  .filter(Boolean)
                  .join(" and ")}{" "}
                missing from the server&rsquo;s .env.
              </>
            ) : (
              "Configuration is ready for controlled provider operations."
            )
          }
        />
      ) : (
        <>
          <div className="pr-card-heading">
            <div>
              <Link href="/platform-admin/integrations">← Integrations</Link>
              <h2>
                <ProviderIcon provider="propertyradar" /> PropertyRadar
              </h2>
              <span className={`platform-status ${integrationStatusTone}`}>
                {integrationStatus}
              </span>
            </div>
            <div className="pr-actions">
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={toggle}
              >
                {data.enabled
                  ? "Pause Provider Access"
                  : "Allow Provider Access"}
              </button>
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => void work("Refresh", async () => {})}
              >
                Refresh
              </button>
            </div>
          </div>
          {data.local_mode && (
            <div className="platform-notice">
              <strong>Local development mode</strong>
              <p>
                {data.webhook_message || "A public webhook URL is configured."}
              </p>
            </div>
          )}
          <IntegrationNavigation
            provider="PropertyRadar"
            activeSection={propertyRadarSectionFor(tab)}
            onSectionChange={(section) =>
              setTab(propertyRadarSections[section][0].id as PropertyRadarTab)
            }
            subtabs={propertyRadarSections[propertyRadarSectionFor(tab)]}
            activeSubtab={tab}
            onSubtabChange={(subtab) => setTab(subtab as PropertyRadarTab)}
            help={<InfoTip text={tabHelp[tab]} />}
          />
          {tab === "Connection" && (
            <ConnectionTab
              data={data}
              onTestConnection={() =>
                run("Test Connection", "/connection/test")
              }
              busy={!!busy}
            />
          )}
          {tab === "Leads" && <LeadsTab data={data} />}
          {tab === "Property Search" && (
            <PropertyRadarDataEndpoint mode="search" draft={draft} />
          )}
          {tab === "Property Details" && (
            <PropertyRadarDataEndpoint mode="details" draft={draft} />
          )}
          {tab === "Contact Enrichment" && (
            <PropertyRadarDataEndpoint mode="enrichment" draft={draft} />
          )}
          {tab === "Leads by Category" && <LeadsByCategoryTab data={data} />}
          {tab === "Leads by Run" && (
            <RecordTable resource="usage" refreshKey={refreshKey} />
          )}
          {(tab === "Configuration" || tab === "Search Settings") && (
            <form onSubmit={save} className="platform-card pr-form">
              {tab === "Configuration" && (
                <>
                  <h3>
                    Connection and geography{" "}
                    <InfoTip text="API token and webhook secret are read from the server's .env — see the Connection tab for readiness." />
                  </h3>
                  <div className="pr-fields">
                    <TextField
                      title="Public webhook URL"
                      value={draft.public_webhook_url}
                      onChange={(v) => change("public_webhook_url", v)}
                      type="url"
                    />
                    <TextField
                      title="State"
                      value={draft.state}
                      onChange={(v) => change("state", v.toUpperCase())}
                      maxLength={2}
                    />
                    <TextField
                      title="City"
                      value={draft.city}
                      onChange={(v) => change("city", v)}
                    />
                    <TextField
                      title="ZIP codes (comma separated)"
                      value={draft.zip_codes.join(",")}
                      onChange={(v) => change("zip_codes", v.split(","))}
                    />
                    <TextField
                      title="County FIPS (optional)"
                      value={draft.county_fips}
                      onChange={(v) => change("county_fips", v)}
                    />
                    <TextField
                      title="Billing-cycle start date"
                      value={draft.billing_cycle_start || ""}
                      type="date"
                      onChange={(v) => change("billing_cycle_start", v || null)}
                    />
                  </div>
                  <h3>Processing and monitoring</h3>
                  <div className="pr-fields">
                    {(
                      [
                        ["enabled", "Allow provider processing"],
                        ["monitor_new_matches", "Monitor New Matches"],
                        ["monitor_status_changes", "Monitor Status Changes"],
                        ["skiptrace_new", "Skip trace new webhook properties"],
                        ["skiptrace_initial", "Skip trace initial import"],
                      ] as const
                    ).map(([key, title]) => (
                      <label className="pr-check" key={key}>
                        <input
                          type="checkbox"
                          checked={draft[key]}
                          onChange={(e) => change(key, e.target.checked)}
                        />
                        {title}
                      </label>
                    ))}
                    <label>
                      Contact selection
                      <select
                        value={draft.contact_mode}
                        onChange={(e) =>
                          change(
                            "contact_mode",
                            e.target.value as Config["contact_mode"],
                          )
                        }
                      >
                        <option value="primary">
                          Primary owner only (recommended)
                        </option>
                        <option value="all">All owners</option>
                      </select>
                    </label>
                  </div>
                  <p>
                    Monthly purchase limits and current usage now live under{" "}
                    <strong>Limits &amp; Usage</strong>.
                  </p>
                </>
              )}
              {tab === "Search Settings" && (
                <>
                  <h3>Property search settings</h3>
                  <div className="pr-fields">
                    <label>
                      Initial rows per category
                      <input
                        type="number"
                        required
                        min={1}
                        max={data.max_initial_rows}
                        value={draft.initial_rows}
                        onChange={(e) =>
                          change("initial_rows", Number(e.target.value))
                        }
                      />
                    </label>
                    <label>
                      High-equity minimum (%)
                      <input
                        type="number"
                        required
                        min={0}
                        max={100}
                        value={draft.high_equity_min}
                        onChange={(e) =>
                          change("high_equity_min", Number(e.target.value))
                        }
                      />
                    </label>
                  </div>
                  <p>
                    Application maximum: {data.max_initial_rows} rows per
                    category. {draft.selected_categories.length} categories ×{" "}
                    {draft.initial_rows} rows = up to{" "}
                    {draft.selected_categories.length * draft.initial_rows}{" "}
                    occurrences before RadarID deduplication.
                  </p>
                  <label className="pr-check">
                    <input
                      type="checkbox"
                      checked={
                        draft.selected_categories.length ===
                        data.categories.length
                      }
                      onChange={(e) =>
                        change(
                          "selected_categories",
                          e.target.checked
                            ? data.categories.map((c) => c.key)
                            : [],
                        )
                      }
                    />
                    Select all categories
                  </label>
                  <div className="pr-categories">
                    {data.categories.map((category) => (
                      <label className="pr-check" key={category.key}>
                        <input
                          type="checkbox"
                          checked={draft.selected_categories.includes(
                            category.key,
                          )}
                          onChange={(e) =>
                            change(
                              "selected_categories",
                              e.target.checked
                                ? [...draft.selected_categories, category.key]
                                : draft.selected_categories.filter(
                                    (k) => k !== category.key,
                                  ),
                            )
                          }
                        />
                        {category.label}
                      </label>
                    ))}
                  </div>
                </>
              )}
              <div className="pr-actions">
                <button className="button" disabled={blocked || !dirty}>
                  Save Configuration
                </button>
                <button
                  type="button"
                  className="button secondary"
                  disabled={!!busy || !dirty}
                  onClick={() => {
                    setDraft(data.config);
                    setMonitoring(null);
                  }}
                >
                  Reset Unsaved Changes
                </button>
                {button("Test Connection", "/connection/test")}
              </div>
              {dirty && (
                <p>
                  Save your changes before running provider endpoints or
                  configuring monitoring.
                </p>
              )}
            </form>
          )}
          {tab === "Lead Lists" && (
            <>
              <div className="platform-metrics">
                <article>
                  <span>Active monitored lists</span>
                  <strong>
                    {(
                      data.metrics.active_monitored_lists ?? 0
                    ).toLocaleString()}
                  </strong>
                </article>
                <article>
                  <span>Monitored properties at last validation</span>
                  <strong>
                    {data.metrics.unique_monitored_properties_at_validation?.toLocaleString() ??
                      "Not validated"}
                  </strong>
                </article>
              </div>
              <section className="platform-card">
                <h3>
                  Monitoring{" "}
                  <InfoTip text="Prepare Lists is a separate manual action. Enable Monitoring checks every prepared list is safely within its limit before turning provider monitoring on." />
                </h3>
                <div className="pr-actions">
                  <button
                    className="button"
                    disabled={blocked || dirty || !data.enabled}
                    onClick={() =>
                      void work("Check monitoring", async () => {
                        const result = await api<MonitoringPreview>(
                          "/monitoring/validate",
                          "POST",
                          {},
                        );
                        setMonitoring(result);
                        if (result.safe)
                          setConfirmation({
                            title: "Enable provider monitoring?",
                            body: `Monitor the full population of ${result.union_count.toLocaleString()} unique properties. ListingIQ will validate all lists again before enabling them.`,
                            confirmLabel: "Enable Monitoring",
                            onConfirm: () =>
                              run("Enable Monitoring", "/monitoring/enable", {
                                preview_id: result.preview_id,
                                confirmed: true,
                              }).then(() => setMonitoring(null)),
                          });
                      })
                    }
                  >
                    Enable Monitoring
                  </button>
                  {button(
                    "Refresh List Status",
                    "/lists/refresh",
                    !data.enabled,
                  )}
                  {button("Pause Monitoring", "/monitoring/pause")}
                  {button(
                    "Recreate Lists",
                    "/lists/prepare",
                    dirty || !data.enabled,
                  )}
                </div>
                {monitoring && (
                  <div className="pr-preview">
                    <p>
                      Full union: {monitoring.union_count.toLocaleString()}{" "}
                      properties · {Object.keys(monitoring.counts).length} lists
                    </p>
                    <p
                      className={
                        monitoring.safe ? "platform-notice" : "platform-error"
                      }
                    >
                      {monitoring.safe
                        ? "Counts are safe. Confirm the dialog to enable monitoring."
                        : monitoring.reason}
                    </p>
                  </div>
                )}
              </section>
              <h3>
                Lists, one per category{" "}
                <InfoTip text="“Properties matched” is the live count PropertyRadar reports for that category's criteria — it updates as properties enter or leave the list." />
              </h3>
              <RecordTable
                resource="lists"
                refreshKey={refreshKey}
                disabled={blocked}
                onPause={(id) =>
                  run("Pause list monitoring", `/lists/${id}/pause`)
                }
              />
            </>
          )}
          {tab === "Webhooks & Events" && (
            <>
              <div className="platform-metrics">
                <article>
                  <span>Webhook events received</span>
                  <strong>
                    {(data.metrics.webhook_events ?? 0).toLocaleString()}
                  </strong>
                </article>
                <article>
                  <span>Duplicate new-match events</span>
                  <strong>
                    {(data.metrics.duplicate_new_matches ?? 0).toLocaleString()}
                  </strong>
                </article>
              </div>
              <section className="platform-card">
                <h3>
                  Webhook delivery{" "}
                  <InfoTip
                    text={`${data.webhook_message ? data.webhook_message + " " : ""}Replace the curl secret placeholder on your machine before sending it. TEST- events are saved and clearly marked; they never contact PropertyRadar.`}
                  />
                </h3>
                <p>
                  Endpoint:{" "}
                  <code>{publicEnv.apiBaseUrl}/webhooks/propertyradar</code> ·
                  Public URL: {data.webhook_url || "Not configured"} · Webhook
                  ID: {data.webhook_id || "Not registered"}
                </p>
                <div className="pr-actions">
                  {button(
                    "Register / Update Webhook",
                    "/webhook/register",
                    !data.enabled ||
                      dirty ||
                      !data.webhook_registration_allowed ||
                      !data.webhook_secret_configured,
                  )}
                  <button
                    className="button secondary"
                    disabled={!!busy}
                    onClick={() =>
                      void work("Copy test curl", async () => {
                        await navigator.clipboard.writeText(
                          `curl --request POST '${publicEnv.apiBaseUrl}/webhooks/propertyradar' --header 'Authorization: Bearer YOUR_LOCAL_WEBHOOK_SECRET' --header 'Content-Type: application/json' --data '{"RadarID":"TEST-PROPERTY-001","ListName":"ListingIQ - Chicago - Expired","TriggerType":"New Match","Change1":"Listing Status:Active:Expired"}'`,
                        );
                      })
                    }
                  >
                    Copy test curl
                  </button>
                  {button("Retry deferred events", "/events/retry")}
                </div>
              </section>
              <h3>Events received</h3>
              <RecordTable resource="events" refreshKey={refreshKey} />
            </>
          )}
          {tab === "Limits & Usage" && (
            <>
              <section className="platform-card">
                <h3>This billing cycle</h3>
                <p>
                  {data.usage.cycle_start
                    ? `${date(data.usage.cycle_start)} → ${date(data.usage.cycle_end)} (end exclusive)`
                    : "Set a billing-cycle start date in Configuration to track cycles."}{" "}
                  · Provider-reported cost: ${data.usage.provider_reported_cost}{" "}
                  {data.usage.no_overage ? "(no overage)" : "(review billing)"}
                </p>
              </section>
              <form onSubmit={save} className="platform-card pr-limits">
                <h3>
                  Monthly limits{" "}
                  <InfoTip text="Each purchase is checked against these caps again at the moment it happens, even if the numbers below are stale." />
                </h3>
                <div className="pr-limits-table">
                  <div className="pr-limits-row pr-limits-head">
                    <span>Limit</span>
                    <span>Used</span>
                    <span>Remaining</span>
                    <span>Monthly cap</span>
                  </div>
                  {(
                    [
                      [
                        "property_export",
                        "export_limit",
                        "Property exports",
                        45000,
                      ],
                      ["phone_unlock", "phone_limit", "Phone unlocks", 2450],
                      ["email_unlock", "email_limit", "Email unlocks", 2450],
                    ] as const
                  ).map(([usageKey, limitKey, title, max]) => (
                    <div className="pr-limits-row" key={usageKey}>
                      <span>{title}</span>
                      <span>{data.usage.used[usageKey].toLocaleString()}</span>
                      <span>
                        {data.usage.remaining[usageKey].toLocaleString()}
                      </span>
                      <input
                        type="number"
                        min={0}
                        max={max}
                        required
                        aria-label={title}
                        value={draft[limitKey]}
                        onChange={(e) =>
                          change(limitKey, Number(e.target.value))
                        }
                      />
                    </div>
                  ))}
                  <div className="pr-limits-row">
                    <span>Unique monitored properties (union)</span>
                    <span>
                      {data.metrics.active_monitored_lists ?? 0} lists live
                    </span>
                    <span>—</span>
                    <input
                      type="number"
                      min={0}
                      max={45000}
                      required
                      aria-label="Unique monitored properties"
                      value={draft.monitored_limit}
                      onChange={(e) =>
                        change("monitored_limit", Number(e.target.value))
                      }
                    />
                  </div>
                </div>
                <p>
                  Per-list hard limit: {data.per_list_limit.toLocaleString()}{" "}
                  leads. Purchase reservations count toward usage even when
                  provider outcomes are uncertain, and deferred records (
                  {data.metrics.deferred_records}) retry after the billing cycle
                  resets.
                </p>
                <div className="pr-actions">
                  <button className="button" disabled={blocked || !dirty}>
                    Save Limits
                  </button>
                  <button
                    type="button"
                    className="button secondary"
                    disabled={!!busy || !dirty}
                    onClick={() => setDraft(data.config)}
                  >
                    Reset Unsaved Changes
                  </button>
                </div>
              </form>
              <section>
                <h3>
                  Skip-tracing jobs ({data.metrics.pending_skiptrace_jobs ?? 0}{" "}
                  pending){" "}
                  <InfoTip text="One row per property queued for owner contact lookup." />
                </h3>
                <RecordTable
                  resource="skiptrace-jobs"
                  refreshKey={refreshKey}
                  onRetry={(id) =>
                    run("Retry skip tracing", `/skiptrace-jobs/${id}/retry`)
                  }
                  disabled={blocked}
                />
              </section>
              <section>
                <h3>
                  Usage records{" "}
                  <InfoTip text="Every purchase or reservation counted against the limits above." />
                </h3>
                <RecordTable resource="usage" refreshKey={refreshKey} />
              </section>
            </>
          )}
          {tab === "API Logs" && (
            <>
              <h3>
                Admin actions{" "}
                <InfoTip text="Every configuration change, import, and monitoring action taken on this integration, with who did it." />
              </h3>
              <RecordTable resource="audit" refreshKey={refreshKey} />
              <h3>
                Provider purchases &amp; API calls{" "}
                <InfoTip text="Every billable call made to PropertyRadar, for reconciliation." />
              </h3>
              <RecordTable resource="api-logs" refreshKey={refreshKey} />
            </>
          )}
        </>
      )}
      <ConfirmDialog
        request={confirmation}
        onClose={() => {
          if (!busyRef.current) setConfirmation(null);
        }}
      />
    </div>
  );
}

function PropertyRadarDataEndpoint({
  mode,
  draft,
}: {
  mode: "search" | "details" | "enrichment";
  draft: Config;
}) {
  const [radarIds, setRadarIds] = useState("");
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const definitions = {
    search: {
      title: "Property Search",
      endpoint: "/properties/search",
      provider: "POST /v1/properties · Purchase=0",
      paid: false,
    },
    details: {
      title: "Property Details",
      endpoint: "/properties/details",
      provider: "POST /v1/properties · Purchase=1",
      paid: true,
    },
    enrichment: {
      title: "Contact Enrichment",
      endpoint: "/contacts/enrich",
      provider: "Owner lookup → phone/email unlock → contact read",
      paid: true,
    },
  } as const;
  const definition = definitions[mode];
  const ids = radarIds
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  const payload =
    mode === "search"
      ? {
          category_ids: draft.selected_categories,
          state: draft.state,
          city: draft.city,
          zip_codes: draft.zip_codes,
          county_fips: draft.county_fips,
          rows_per_category: draft.initial_rows,
        }
      : mode === "details"
        ? {
            radar_ids: ids,
            category_ids: draft.selected_categories,
            confirmed: false,
            reason: "",
          }
        : { radar_ids: ids, confirmed: false, reason: "" };
  const runEndpoint = async () => {
    let request: Record<string, unknown> = payload;
    if (definition.paid) {
      const reason = window.prompt(`Reason for running ${definition.title}:`);
      if (!reason || !window.confirm(`Run ${definition.title} now?`)) return;
      request = { ...payload, confirmed: true, reason };
    }
    setBusy(true);
    setError("");
    try {
      setResult(await api(definition.endpoint, "POST", request));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Operation failed.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="platform-card pr-form">
      <div className="dm-endpoint-head">
        <div>
          <h3>{definition.title}</h3>
          <p>
            Internal:{" "}
            <code>
              POST /api/v1/integrations/propertyradar{definition.endpoint}
            </code>
          </p>
          <p>Provider: {definition.provider}</p>
        </div>
        <span
          className={`integration-cost-badge ${definition.paid ? "paid" : "free"}`}
        >
          {definition.paid ? "Paid" : "Free"}
        </span>
      </div>
      {mode !== "search" && (
        <div className="pr-fields">
          <label>
            RadarIDs (comma separated)
            <input
              value={radarIds}
              onChange={(event) => setRadarIds(event.target.value)}
              placeholder="P123, P456"
            />
          </label>
        </div>
      )}
      <details className="bd-json">
        <summary>Request JSON preview</summary>
        <pre>{JSON.stringify(payload, null, 2)}</pre>
      </details>
      <IntegrationActionGuidance kind={definition.paid ? "paid" : "free"}>
        {mode === "search"
          ? "Returns and deduplicates RadarIDs only. It does not purchase details or contacts."
          : mode === "details"
            ? "Purchases property details only. Contact enrichment remains a separate action."
            : "Accepts only previously saved properties and separately enforces phone and email allowances."}
      </IntegrationActionGuidance>
      {error && <p className="platform-error">{error}</p>}
      <div className="pr-actions">
        <button
          className="button"
          disabled={busy || (mode !== "search" && ids.length === 0)}
          onClick={() => void runEndpoint()}
        >
          {busy ? "Working…" : `Run ${definition.title}`}
        </button>
      </div>
      {result !== null && (
        <div className="dm-result">
          <div className="dm-result-toolbar">
            <strong>Response</strong>
          </div>
          <pre>{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
    </section>
  );
}

function ConnectionTab({
  data,
  onTestConnection,
  busy,
}: {
  data: Summary;
  onTestConnection: () => Promise<void>;
  busy: boolean;
}) {
  const checks = [
    {
      label: "API token",
      ok: data.token_configured,
      detail: data.token_configured
        ? "PROPERTYRADAR_API_TOKEN is set on the server."
        : "Set PROPERTYRADAR_API_TOKEN in the server's .env.",
    },
    {
      label: "Webhook secret",
      ok: data.webhook_secret_configured,
      detail: data.webhook_secret_configured
        ? "PROPERTYRADAR_WEBHOOK_SECRET is set (32+ characters)."
        : "Set PROPERTYRADAR_WEBHOOK_SECRET in the server's .env — at least 32 random characters.",
    },
    {
      label: "Public webhook URL",
      ok: data.webhook_registration_allowed,
      detail: data.webhook_registration_allowed
        ? `PropertyRadar can reach ${data.webhook_url}.`
        : data.webhook_message ||
          "Needed before PropertyRadar can deliver live events; not required for local testing via Postman.",
    },
    {
      label: "Connection verified",
      ok: !!data.last_successful_connection_at && !data.last_error,
      detail: data.last_error
        ? data.last_error
        : data.last_successful_connection_at
          ? `Verified ${date(data.last_successful_connection_at)}.`
          : "Not tested yet — use Test Connection below.",
    },
    {
      label: "Provider processing allowed",
      ok: data.enabled,
      detail: data.enabled
        ? "Processing leads and events."
        : "Turn on under Configuration to start processing.",
    },
  ];
  const ready = checks.every((c) => c.ok);
  return (
    <section className="platform-card pr-connection-card">
      <div className={`pr-ready-banner ${ready ? "ok" : "pending"}`}>
        {ready
          ? "Ready — PropertyRadar is fully configured."
          : `${checks.filter((c) => !c.ok).length} step${checks.filter((c) => !c.ok).length === 1 ? "" : "s"} left before PropertyRadar is fully set up.`}
      </div>
      <ul className="pr-checklist">
        {checks.map((c) => (
          <li key={c.label} className={c.ok ? "ok" : "pending"}>
            <span className="pr-checklist-mark" aria-hidden="true">
              {c.ok ? "✓" : "○"}
            </span>
            <div>
              <strong>{c.label}</strong>
              <p>{c.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <IntegrationActionGuidance kind="free">
        Testing the connection validates the server-side setup without
        purchasing property or contact data.
      </IntegrationActionGuidance>
      <div className="pr-actions">
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={() => void onTestConnection()}
        >
          Test Connection
        </button>
      </div>
    </section>
  );
}

function LeadsTab({ data }: { data: Summary }) {
  const activity = data.lead_activity;
  const metric = (key: string) => data.metrics[key] ?? 0;
  return (
    <div className="platform-metrics">
      <article>
        <span>Total properties</span>
        <strong>{metric("unique_properties").toLocaleString()}</strong>
      </article>
      <article>
        <span>New properties this week</span>
        <strong>{(activity?.new_leads_7d ?? 0).toLocaleString()}</strong>
      </article>
      <article>
        <span>New properties this month</span>
        <strong>{(activity?.new_leads_30d ?? 0).toLocaleString()}</strong>
      </article>
      <article>
        <span>Property updates this week</span>
        <strong>{(activity?.lead_updates_7d ?? 0).toLocaleString()}</strong>
      </article>
      <article>
        <span>Property updates this month</span>
        <strong>{(activity?.lead_updates_30d ?? 0).toLocaleString()}</strong>
      </article>
      <article>
        <span>From initial import</span>
        <strong>{metric("initial_properties").toLocaleString()}</strong>
      </article>
      <article>
        <span>From webhook matches</span>
        <strong>{metric("new_webhook_properties").toLocaleString()}</strong>
      </article>
    </div>
  );
}

function LeadsByCategoryTab({ data }: { data: Summary }) {
  const jobs = data.jobs_by_category ?? [];
  if (!jobs.length)
    return (
      <p className="pr-empty">
        No category runs yet. Open Data, then Lists, to prepare provider lists.
      </p>
    );
  return (
    <div className="platform-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>List</th>
            <th>Properties matched</th>
            <th>Monitored</th>
            <th>Last synced</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.list_name}>
              <td>{job.category}</td>
              <td>{job.list_name}</td>
              <td>{job.total_count?.toLocaleString() ?? "—"}</td>
              <td>{job.is_monitored ? "Yes" : "No"}</td>
              <td>{date(job.last_synced_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TextField({
  title,
  value,
  onChange,
  type = "text",
  maxLength,
}: {
  title: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  maxLength?: number;
}) {
  return (
    <label>
      {title}
      <input
        type={type}
        value={value}
        maxLength={maxLength}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
const columns: Record<string, string[]> = {
  lists: [
    "category",
    "provider_list_id",
    "list_name",
    "total_count",
    "is_monitored",
    "automation_status",
    "last_synced_at",
    "error_message",
  ],
  events: [
    "received_at",
    "radar_id",
    "list_name",
    "trigger_type",
    "is_test",
    "is_duplicate_property",
    "is_retry_duplicate",
    "change_1",
    "change_2",
    "change_3",
    "processing_status",
    "skiptrace_status",
    "error_message",
    "raw_payload",
  ],
  "skiptrace-jobs": [
    "radar_id",
    "status",
    "selected_contact_mode",
    "queued_at",
    "deferred_until",
    "error_message",
  ],
  usage: [
    "created_at",
    "billing_cycle",
    "usage_type",
    "radar_id",
    "person_key",
    "preview",
    "purchased",
    "result_count",
    "quantity_free_remaining",
    "total_cost",
    "status",
  ],
  "api-logs": [
    "created_at",
    "endpoint",
    "usage_type",
    "preview",
    "purchased",
    "result_count",
    "total_cost",
    "request_id",
    "status",
    "error_message",
  ],
  audit: [
    "created_at",
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "detail",
  ],
};
function RecordTable({
  resource,
  refreshKey,
  onRetry,
  onPause,
  disabled,
}: {
  resource: string;
  refreshKey: number;
  onRetry?: (id: number) => Promise<void>;
  onPause?: (id: number) => Promise<void>;
  disabled?: boolean;
}) {
  const [records, setRecords] = useState<Records | null>(null);
  const [offset, setOffset] = useState(0);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [filter, setFilter] = useState({ q: "", status: "" });
  const [error, setError] = useState("");
  const [loadedKey, setLoadedKey] = useState("");
  const queryKey = JSON.stringify([resource, offset, filter, refreshKey]);
  const loading = loadedKey !== queryKey;
  useEffect(() => {
    let alive = true;

    void api<Records>(
      `/${resource}?offset=${offset}&limit=25&q=${encodeURIComponent(filter.q)}&status=${encodeURIComponent(filter.status)}`,
    )
      .then((value) => {
        if (alive) {
          setRecords(value);
          setError("");
        }
      })
      .catch((reason) => {
        if (alive) setError(reason.message);
      })
      .finally(() => {
        if (alive) setLoadedKey(queryKey);
      });
    return () => {
      alive = false;
    };
  }, [resource, offset, filter, refreshKey, queryKey]);
  return (
    <section className="pr-records">
      <form
        className="platform-search"
        onSubmit={(e) => {
          e.preventDefault();
          setOffset(0);
          setFilter({ q, status });
        }}
      >
        <input
          aria-label={`Search ${resource}`}
          placeholder="Search RadarID, list or type"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <input
          aria-label="Status filter"
          placeholder="Status (e.g. pending)"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        />
        <button className="button secondary" disabled={loading}>
          Filter
        </button>
      </form>
      {error && (
        <p role="alert" className="platform-error">
          {error}
        </p>
      )}
      {loading && <p role="status">Loading records…</p>}
      <div className="platform-table-wrap">
        <table>
          <thead>
            <tr>
              {columns[resource].map((key) => (
                <th key={key}>{label(key)}</th>
              ))}
              {(onRetry || onPause) && <th>Action</th>}
            </tr>
          </thead>
          <tbody>
            {records?.items.map((row) => (
              <tr key={row.id}>
                {columns[resource].map((key) => (
                  <td key={key}>
                    {key === "raw_payload" ? (
                      <details>
                        <summary>View payload</summary>
                        <pre>{JSON.stringify(row[key], null, 2)}</pre>
                      </details>
                    ) : typeof row[key] === "boolean" ? (
                      row[key] ? (
                        "Yes"
                      ) : (
                        "No"
                      )
                    ) : (
                      String(row[key] ?? "—")
                    )}
                  </td>
                ))}
                {onPause && (
                  <td>
                    <button
                      className="table-action"
                      disabled={disabled || !row.is_monitored}
                      onClick={() => void onPause(row.id)}
                    >
                      Pause
                    </button>
                  </td>
                )}
                {onRetry && (
                  <td>
                    <button
                      className="table-action"
                      disabled={disabled || row.status === "completed"}
                      onClick={() => void onRetry(row.id)}
                    >
                      Retry
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && records?.total === 0 && (
          <p className="pr-empty">No records yet.</p>
        )}
      </div>
      <div className="pr-actions">
        <button
          className="button secondary"
          disabled={loading || offset === 0}
          onClick={() => setOffset(Math.max(0, offset - 25))}
        >
          Previous
        </button>
        <span>
          {records?.total ?? 0} records · Page {offset / 25 + 1}
        </span>
        <button
          className="button secondary"
          disabled={loading || !records || offset + 25 >= records.total}
          onClick={() => setOffset(offset + 25)}
        >
          Next
        </button>
      </div>
    </section>
  );
}
