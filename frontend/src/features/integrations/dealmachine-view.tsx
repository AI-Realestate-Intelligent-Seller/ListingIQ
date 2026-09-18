"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Toast } from "@/components/toast/toast";
import { dealMachineApi as api } from "./dealmachine-api";
import {
  ProviderIcon,
  ProviderIntegrationCard,
} from "./provider-integration-card";
import {
  IntegrationNavigation,
  type IntegrationSection,
  type IntegrationSubtab,
} from "./integration-navigation";
import {
  IntegrationActionGuidance,
  IntegrationCostBadge,
} from "./integration-cost";

type Json = Record<string, unknown>;
type CreditLimits = {
  monthly_total_credit_cap: number;
  monthly_property_credit_cap: number;
  monthly_people_credit_cap: number;
  per_run_property_limit: number;
  per_run_people_limit: number;
  rows_per_category: number;
  maximum_categories_per_run: number;
  daily_request_limit: number;
  minimum_remaining_credit_reserve: number;
  stop_on_warning: boolean;
};
type Summary = {
  status: string;
  enabled: boolean;
  connection_status: string;
  credential: { configured: boolean; masked: string | null };
  encryption_configured: boolean;
  plan: string;
  available_monthly_credits: number;
  property_credits_used: number;
  people_credits_used: number;
  last_tested_at: string | null;
  last_successful_sync_at: string | null;
  next_scheduled_sync_at: string | null;
  last_error: string | null;
  settings: Json & { limits: CreditLimits; selected_fields: string[] };
  categories: Array<{
    key: string;
    label: string;
    enabled: boolean;
    support_status: string;
    configuration: Json;
    last_run_at: string | null;
    next_run_at: string | null;
    last_result_count: number;
    new_match_count: number;
    changed_record_count: number;
    credits_used: number;
  }>;
  counts: { properties: number; runs: number; queued: number };
  export_running: boolean;
};

type Tab =
  | "Account"
  | "Filters"
  | "Fields"
  | "Usage"
  | "Property Count"
  | "Cost Estimate"
  | "Property Search"
  | "Property Details"
  | "Contact Enrichment"
  | "Create List"
  | "List Status"
  | "List Items"
  | "Activity"
  | "Property Export"
  | "History & Saved Files";
const dealMachineSections: Record<
  IntegrationSection,
  readonly IntegrationSubtab[]
> = {
  Connection: [{ id: "Account", label: "Connection" }],
  Configuration: [
    { id: "Filters", label: "Filters" },
    { id: "Fields", label: "Fields" },
  ],
  Data: [
    { id: "Property Search", label: "Property Search" },
    { id: "Property Details", label: "Property Details" },
    { id: "Contact Enrichment", label: "Contact Enrichment" },
    { id: "Property Count", label: "Count" },
    { id: "Cost Estimate", label: "Cost Estimate" },
    { id: "Create List", label: "Create List" },
    { id: "List Status", label: "List Status" },
    { id: "List Items", label: "List Items" },
    { id: "Property Export", label: "Export" },
  ],
  "Usage & Limits": [{ id: "Usage", label: "Usage & Limits" }],
  Activity: [{ id: "Activity", label: "Provider Activity" }],
  History: [{ id: "History & Saved Files", label: "Runs & Saved Files" }],
};
const dealMachineSectionFor = (tab: Tab) =>
  (Object.entries(dealMachineSections).find(([, subtabs]) =>
    subtabs.some((subtab) => subtab.id === tab),
  )?.[0] || "Connection") as IntegrationSection;
const defaultSearch = {
  category_ids: [],
  filters: [],
  locations: [],
  fields: [],
  sort: [],
  page: 1,
  per_page: 20,
  confirmed: false,
  reason: "",
};
const definitions: Record<
  Tab,
  { method: string; external: string; paid: boolean; copy: string }
> = {
  Account: {
    method: "POST",
    external: "GET /v1/account",
    paid: false,
    copy: "Validate the server-side API key and load organization details.",
  },
  Filters: {
    method: "GET",
    external: "GET /v1/filters?source_type=properties",
    paid: false,
    copy: "Discover and cache supported property filters before mapping categories.",
  },
  Fields: {
    method: "GET",
    external: "GET /v1/fields?source_type=properties",
    paid: false,
    copy: "Discover and cache selectable property output fields.",
  },
  Usage: {
    method: "GET",
    external: "GET /v1/usage",
    paid: false,
    copy: "Review provider allowance and ListingIQ safety limits.",
  },
  "Property Count": {
    method: "POST",
    external: "POST /v1/properties/search/count",
    paid: false,
    copy: "Count matching properties without saving records or consuming credits.",
  },
  "Cost Estimate": {
    method: "POST",
    external: "POST /v1/properties/search · estimate_cost=true",
    paid: false,
    copy: "Preview page and all-pages credits. Property-only output is enforced by the API.",
  },
  "Property Search": {
    method: "POST",
    external: "POST /v1/properties/search",
    paid: true,
    copy: "Fetch property-only records, persist immutable snapshots, deduplicate, and queue new properties.",
  },
  "Property Details": {
    method: "POST",
    external: "POST /v1/properties/ids",
    paid: true,
    copy: "Fetch property details for selected, locally deduplicated DealMachine property IDs. Contact data is never requested here.",
  },
  "Contact Enrichment": {
    method: "POST",
    external: "POST /v1/properties/ids · contact_audience=owners",
    paid: true,
    copy: "Fetch owner contacts only for selected properties already saved by Property Search.",
  },
  "Create List": {
    method: "POST",
    external: "POST /v1/lists",
    paid: false,
    copy: "Create a DealMachine snapshot list from filters or known property IDs.",
  },
  "List Status": {
    method: "GET",
    external: "GET /v1/lists/:id",
    paid: false,
    copy: "Poll a saved list conservatively; responses are cached for at least three seconds.",
  },
  "List Items": {
    method: "POST / DELETE",
    external: "POST / DELETE /v1/lists/:id/items",
    paid: false,
    copy: "Add or remove list membership while retaining before/after history.",
  },
  Activity: {
    method: "POST / GET",
    external: "POST /v1/activity/search · GET /v1/activity/:id",
    paid: false,
    copy: "Reconcile provider activity with local execution history.",
  },
  "Property Export": {
    method: "POST",
    external: "POST /v1/properties/export",
    paid: true,
    copy: "Run one confirmed export, download it immediately, and retain file metadata.",
  },
  "History & Saved Files": {
    method: "GET",
    external: "Local append-only history",
    paid: false,
    copy: "Inspect requests, responses, processing summaries, credits, errors, and saved files.",
  },
};
const actionLabels: Record<Tab, string> = {
  Account: "Test Connection",
  Filters: "Load DealMachine Filters",
  Fields: "Load DealMachine Fields",
  Usage: "Refresh Credit Usage",
  "Property Count": "Check Property Count",
  "Cost Estimate": "Estimate Credit Cost",
  "Property Search": "Search Properties",
  "Property Details": "Load Property Details",
  "Contact Enrichment": "Enrich Contacts",
  "Create List": "Create List",
  "List Status": "Check List Status",
  "List Items": "Update List Items",
  Activity: "Search Provider Activity",
  "Property Export": "Export Properties",
  "History & Saved Files": "Refresh History",
};

const starter = (tab: Tab): Json => {
  if (["Property Count", "Cost Estimate", "Property Search"].includes(tab))
    return { ...defaultSearch };
  if (tab === "Property Details")
    return {
      dm_property_ids: [],
      fields: [],
      confirmed: false,
      reason: "",
    };
  if (tab === "Contact Enrichment")
    return {
      dm_property_ids: [],
      contact_audience: "owners",
      fields: [],
      confirmed: false,
      force: false,
      reason: "",
    };
  if (tab === "Create List")
    return {
      name: "ListingIQ properties",
      category_ids: [],
      filters: [],
      locations: [],
      record_ids: [],
    };
  if (tab === "List Items")
    return {
      list_id: "",
      ids: [],
      id_type: "internal_property_id",
      confirmed: false,
      reason: "",
    };
  if (tab === "Activity")
    return {
      query: "",
      filters: {},
      date_range: {},
      entity_ids: [],
      sort: [],
      page: 1,
      per_page: 25,
      activity_id: "",
    };
  if (tab === "Property Export")
    return {
      ...defaultSearch,
      list_id: null,
      expected_count: 0,
      estimated_credits: 0,
    };
  if (tab === "List Status") return { list_id: "" };
  return {};
};
const when = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";

export function DealMachineView({ detail = false }: { detail?: boolean }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [tab, setTab] = useState<Tab>("Account");
  const [request, setRequest] = useState(() =>
    JSON.stringify(starter("Account"), null, 2),
  );
  const [result, setResult] = useState<unknown>(null);
  const [history, setHistory] = useState<Json[]>([]);
  const [view, setView] = useState<"table" | "json">("table");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const reload = useCallback(
    async () => setSummary(await api.overview<Summary>()),
    [],
  );
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial authenticated API hydration
    void reload().catch((reason) => setError(reason.message));
  }, [reload]);
  const integrationStatus =
    summary?.status === "Error"
      ? "Error"
      : summary?.connection_status === "connected"
        ? "Ready"
        : summary?.credential.configured
          ? "Needs testing"
          : "Needs setup";
  const statusTone =
    integrationStatus === "Ready"
      ? "on"
      : integrationStatus === "Error"
        ? "off"
        : "neutral";
  async function work(label: string, action: () => Promise<unknown>) {
    if (busy) return;
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const value = await action();
      setResult(value);
      await reload();
      setNotice(`${label} completed.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Operation failed.");
    } finally {
      setBusy("");
    }
  }
  async function execute() {
    let body: Json;
    try {
      body = JSON.parse(request) as Json;
    } catch {
      setError("Request JSON is invalid.");
      return;
    }
    const definition = definitions[tab];
    if (definition.paid) {
      const reason = window.prompt(
        "Audit reason for this paid DealMachine call:",
      );
      if (
        !reason ||
        !window.confirm(
          "This operation may consume DealMachine credits. Continue?",
        )
      )
        return;
      body = { ...body, confirmed: true, reason };
    }
    await work(actionLabels[tab], async () => {
      switch (tab) {
        case "Account":
          return api.testConnection();
        case "Filters":
          return api.filters(true);
        case "Fields":
          return api.fields(true);
        case "Usage":
          return api.usage();
        case "Property Count":
          return api.propertyCount(body);
        case "Cost Estimate":
          return api.propertyEstimate(body);
        case "Property Search":
          return api.propertySearch(body);
        case "Property Details":
          return api.propertyDetails(body);
        case "Contact Enrichment":
          return api.contactEnrichment(body);
        case "Create List":
          return api.createList(body);
        case "List Status":
          return api.listStatus(String(body.list_id || ""));
        case "List Items":
          return body.remove
            ? api.removeListItems(String(body.list_id || ""), body)
            : api.addListItems(String(body.list_id || ""), body);
        case "Activity":
          return body.activity_id
            ? api.activityDetail(String(body.activity_id))
            : api.activitySearch(body);
        case "Property Export":
          return api.propertyExport(body);
        case "History & Saved Files":
          return api.history();
      }
    });
  }
  useEffect(() => {
    if (!detail) return;
    void api
      .history<{ items: Json[] }>()
      .then((value) => setHistory(value.items))
      .catch(() => undefined);
  }, [detail, result]);
  if (!summary)
    return (
      <section className="pr-workspace">
        <p className={error ? "platform-error" : undefined}>
          {error || "Loading DealMachine…"}
        </p>
        <button className="button secondary" onClick={() => void reload()}>
          Retry
        </button>
      </section>
    );
  const requestBodyAvailable = ![
    "Filters",
    "Fields",
    "Usage",
    "History & Saved Files",
  ].includes(tab);
  let requestObject: Json = starter(tab);
  try {
    requestObject = JSON.parse(request) as Json;
  } catch {
    // Execute reports invalid JSON. Form controls use the safe starter shape.
  }
  const selectedPropertyIds = Array.isArray(requestObject.dm_property_ids)
    ? requestObject.dm_property_ids.map(String).join(", ")
    : "";
  const updateRequest = (patch: Json) =>
    setRequest(JSON.stringify({ ...requestObject, ...patch }, null, 2));
  if (!detail)
    return (
      <ProviderIntegrationCard
        provider="dealmachine"
        name="DealMachine"
        href="/operations/integrations/dealmachine"
        status={integrationStatus}
        statusTone={statusTone}
        description="Manual property discovery, list management, credit checks, and controlled contact enrichment."
        metrics={[
          {
            label: "Connection",
            value: summary.connection_status.replaceAll("_", " "),
          },
          {
            label: "Credential",
            value: summary.credential.masked || "Not configured",
          },
          {
            label: "Available credits",
            value: summary.available_monthly_credits.toLocaleString(),
          },
          {
            label: "Property / people used",
            value: `${summary.property_credits_used} / ${summary.people_credits_used}`,
          },
          {
            label: "Last activity",
            value: when(summary.last_successful_sync_at),
          },
          { label: "Last error", value: summary.last_error || "None" },
        ]}
        notice={
          summary.last_error ||
          "Manual actions only. Nothing runs in the background."
        }
        noticeTone={summary.last_error ? "error" : "neutral"}
      >
        {error && (
          <Toast message={error} tone="error" onDone={() => setError("")} />
        )}
      </ProviderIntegrationCard>
    );
  return (
    <div className="pr-workspace dm-workspace">
      {error && (
        <Toast message={error} tone="error" onDone={() => setError("")} />
      )}
      {notice && (
        <Toast message={notice} tone="success" onDone={() => setNotice("")} />
      )}
      <div className="pr-card-heading">
        <div>
          <Link href="/platform-admin/integrations">← Integrations</Link>
          <h2>
            <ProviderIcon provider="dealmachine" /> DealMachine
          </h2>
          <span className={`platform-status ${statusTone}`}>
            {integrationStatus}
          </span>
        </div>
        <div className="pr-actions">
          <span>Manual Actions Only</span>
          <button
            className="button secondary"
            disabled={!!busy}
            onClick={() => void reload()}
          >
            Refresh
          </button>
        </div>
      </div>
      <div className="platform-notice">
        <strong>No automation</strong>
        <p>
          Nothing runs in the background. Choose an endpoint, review the
          prepared request, and click the action yourself. Paid calls always
          show a confirmation and require a reason.
        </p>
      </div>
      <IntegrationNavigation
        provider="DealMachine"
        activeSection={dealMachineSectionFor(tab)}
        onSectionChange={(section) => {
          const destination = dealMachineSections[section][0].id as Tab;
          setTab(destination);
          setRequest(JSON.stringify(starter(destination), null, 2));
          setResult(null);
        }}
        subtabs={dealMachineSections[dealMachineSectionFor(tab)]}
        activeSubtab={tab}
        onSubtabChange={(subtab) => {
          const destination = subtab as Tab;
          setTab(destination);
          setRequest(JSON.stringify(starter(destination), null, 2));
          setResult(null);
        }}
      />
      {tab === "Account" && (
        <Account
          summary={summary}
          busy={!!busy}
          onTestConnection={() => void execute()}
        />
      )}
      {tab === "Usage" && (
        <>
          <Usage summary={summary} />
          <SafetySettings
            summary={summary}
            reload={reload}
            setError={setError}
            setNotice={setNotice}
          />
        </>
      )}
      {tab !== "Account" && (
        <section className="platform-card dm-endpoint">
          <div className="dm-endpoint-head">
            <div>
              <span className="dm-method">{definitions[tab].method}</span>
              <code>{definitions[tab].external}</code>
              <p>{definitions[tab].copy}</p>
            </div>
            <IntegrationCostBadge
              kind={definitions[tab].paid ? "paid" : "free"}
            />
          </div>
          <IntegrationActionGuidance
            kind={definitions[tab].paid ? "paid" : "free"}
          >
            {definitions[tab].paid
              ? "Nothing runs automatically. You will confirm the credit cost and enter an audit reason before this request is sent."
              : "Nothing runs automatically, and this action does not use DealMachine credits."}
          </IntegrationActionGuidance>
          {(tab === "Property Details" || tab === "Contact Enrichment") && (
            <div className="pr-fields">
              <label>
                DealMachine property IDs
                <input
                  aria-label="DealMachine property IDs"
                  value={selectedPropertyIds}
                  placeholder="DM123, DM456"
                  onChange={(event) =>
                    updateRequest({
                      dm_property_ids: event.target.value
                        .split(",")
                        .map((value) => value.trim())
                        .filter(Boolean),
                    })
                  }
                />
                <small>
                  Use IDs already saved by Property Search. Separate multiple
                  IDs with commas.
                </small>
              </label>
              {tab === "Contact Enrichment" && (
                <>
                  <label>
                    Contact audience
                    <select
                      aria-label="Contact audience"
                      value={String(requestObject.contact_audience || "owners")}
                      onChange={(event) =>
                        updateRequest({ contact_audience: event.target.value })
                      }
                    >
                      <option value="owners">Owners</option>
                      <option value="owners_and_family">
                        Owners and family
                      </option>
                      <option value="renters">Renters</option>
                      <option value="residents">Residents</option>
                    </select>
                  </label>
                  <label className="pr-check">
                    <input
                      type="checkbox"
                      checked={Boolean(requestObject.force)}
                      onChange={(event) =>
                        updateRequest({ force: event.target.checked })
                      }
                    />
                    Re-enrich properties that already have contacts
                  </label>
                </>
              )}
            </div>
          )}
          {requestBodyAvailable && (
            <details className="dm-request-preview">
              <summary>Advanced request JSON (optional)</summary>
              <label className="dm-editor-label">
                <span className="sr-only">Request JSON</span>
                <textarea
                  aria-label="Request JSON"
                  className="dm-editor"
                  value={request}
                  onChange={(event) => setRequest(event.target.value)}
                  spellCheck={false}
                />
              </label>
            </details>
          )}
          <div className="pr-actions">
            <button
              className="button"
              disabled={
                !!busy ||
                (definitions[tab].paid &&
                  summary.connection_status !== "connected")
              }
              onClick={() => void execute()}
            >
              {busy || actionLabels[tab]}
            </button>
            {requestBodyAvailable && (
              <button
                className="button secondary"
                onClick={() =>
                  setRequest(JSON.stringify(starter(tab), null, 2))
                }
              >
                Reset Request
              </button>
            )}
            <button
              className="button secondary"
              onClick={() =>
                downloadJson(
                  result,
                  `${tab.toLowerCase().replaceAll(" ", "-")}.json`,
                )
              }
              disabled={!result}
            >
              Download result
            </button>
          </div>
          {busy && (
            <p className="platform-notice" role="status">
              {busy} in progress…
            </p>
          )}
          {result != null && (
            <Result value={result} view={view} setView={setView} />
          )}
        </section>
      )}
      {tab === "Filters" && (
        <Categories summary={summary} reload={reload} setError={setError} />
      )}
      {tab === "History & Saved Files" && (
        <section>
          <div className="dm-section-title">
            <h3>Execution History</h3>
            <span>{history.length} recent executions</span>
          </div>
          <History rows={history} />
        </section>
      )}
    </div>
  );
}

function Account({
  summary,
  busy,
  onTestConnection,
}: {
  summary: Summary;
  busy: boolean;
  onTestConnection: () => void;
}) {
  const checks = [
    {
      label: "API key",
      ok: summary.credential.configured,
      detail: summary.credential.configured
        ? `DEALMACHINE_API_KEY is configured (${summary.credential.masked}).`
        : "Set DEALMACHINE_API_KEY in the server's .env.",
    },
    {
      label: "Credential protection",
      ok: true,
      detail: summary.encryption_configured
        ? "Encrypted credential storage is ready. The key is never returned to the browser."
        : "Server environment only. The key is never returned to the browser.",
    },
    {
      label: "Connection verified",
      ok: summary.connection_status === "connected",
      detail:
        summary.connection_status === "connected"
          ? `Verified ${when(summary.last_tested_at)}.`
          : summary.last_error || "Not tested yet — use Test Connection below.",
    },
    {
      label: "Provider account loaded",
      ok: summary.plan !== "Unknown",
      detail:
        summary.plan !== "Unknown"
          ? `${summary.plan} plan · ${summary.available_monthly_credits.toLocaleString()} available credits.`
          : "Test the connection to load plan and available-credit information.",
    },
    {
      label: "Manual execution mode",
      ok: true,
      detail:
        "Nothing runs in the background. Paid actions require confirmation and an audit reason.",
    },
  ];
  const remaining = checks.filter((check) => !check.ok).length;
  const ready = remaining === 0;
  return (
    <section className="platform-card pr-connection-card">
      <div className={`pr-ready-banner ${ready ? "ok" : "pending"}`}>
        {ready
          ? "Ready — DealMachine is fully configured."
          : `${remaining} step${remaining === 1 ? "" : "s"} left before DealMachine is fully set up.`}
      </div>
      <ul className="pr-checklist">
        {checks.map((check) => (
          <li key={check.label} className={check.ok ? "ok" : "pending"}>
            <span className="pr-checklist-mark" aria-hidden="true">
              {check.ok ? "✓" : "○"}
            </span>
            <div>
              <strong>{check.label}</strong>
              <p>{check.detail}</p>
            </div>
          </li>
        ))}
      </ul>
      <div className="pr-actions">
        <button
          type="button"
          className="button secondary"
          disabled={busy}
          onClick={onTestConnection}
        >
          Test Connection
        </button>
      </div>
    </section>
  );
}
function Usage({ summary }: { summary: Summary }) {
  const accountLoaded = summary.plan !== "Unknown";
  return (
    <div className="platform-metrics dm-summary">
      <article>
        <span>Plan</span>
        <strong>{accountLoaded ? summary.plan : "Not loaded"}</strong>
      </article>
      <article>
        <span>Available credits</span>
        <strong>
          {accountLoaded
            ? summary.available_monthly_credits.toLocaleString()
            : "Refresh usage"}
        </strong>
      </article>
      <article>
        <span>Property credits used</span>
        <strong>{summary.property_credits_used}</strong>
      </article>
      <article>
        <span>Contact credits used</span>
        <strong>{summary.people_credits_used}</strong>
      </article>
    </div>
  );
}
function SafetySettings({
  summary,
  reload,
  setError,
  setNotice,
}: {
  summary: Summary;
  reload: () => Promise<void>;
  setError: (value: string) => void;
  setNotice: (value: string) => void;
}) {
  const [draft, setDraft] = useState<CreditLimits>(() => ({
    ...summary.settings.limits,
  }));
  const change = (key: keyof CreditLimits, value: number | boolean) =>
    setDraft((current) => ({ ...current, [key]: value }));
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    try {
      await api.saveSettings({
        limits: draft,
        selected_fields: summary.settings.selected_fields,
        api_key: null,
      });
      await reload();
      setNotice("Credit safety settings saved.");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Settings update failed.",
      );
    }
  }
  return (
    <form className="platform-card pr-form dm-settings" onSubmit={save}>
      <h3>Spending safety limits</h3>
      <p>
        These are maximums, not automatic spending. DealMachine calls happen
        only when you click an action, and the backend blocks anything above
        these limits.
      </p>
      <h3>Monthly credit caps</h3>
      <div className="pr-fields">
        <CreditLimitField
          label="Total credits"
          value={draft.monthly_total_credit_cap}
          onChange={(value) => change("monthly_total_credit_cap", value)}
        />
        <CreditLimitField
          label="Property credits"
          value={draft.monthly_property_credit_cap}
          onChange={(value) => change("monthly_property_credit_cap", value)}
        />
        <CreditLimitField
          label="Contact credits"
          value={draft.monthly_people_credit_cap}
          onChange={(value) => change("monthly_people_credit_cap", value)}
        />
      </div>
      <h3>Per-action limits</h3>
      <div className="pr-fields">
        <CreditLimitField
          label="Properties per action"
          value={draft.per_run_property_limit}
          min={1}
          max={100000}
          onChange={(value) => change("per_run_property_limit", value)}
        />
        <CreditLimitField
          label="Contacts per action"
          value={draft.per_run_people_limit}
          max={100000}
          onChange={(value) => change("per_run_people_limit", value)}
        />
        <CreditLimitField
          label="Rows per category"
          value={draft.rows_per_category}
          min={1}
          max={250}
          onChange={(value) => change("rows_per_category", value)}
        />
        <CreditLimitField
          label="Categories per bulk search"
          value={draft.maximum_categories_per_run}
          min={1}
          max={21}
          onChange={(value) => change("maximum_categories_per_run", value)}
        />
      </div>
      <h3>Account protection</h3>
      <div className="pr-fields">
        <CreditLimitField
          label="Requests per day"
          value={draft.daily_request_limit}
          min={1}
          max={5000}
          onChange={(value) => change("daily_request_limit", value)}
        />
        <CreditLimitField
          label="Credits to keep in reserve"
          value={draft.minimum_remaining_credit_reserve}
          onChange={(value) =>
            change("minimum_remaining_credit_reserve", value)
          }
        />
        <label className="pr-check">
          <input
            type="checkbox"
            checked={draft.stop_on_warning}
            onChange={(event) =>
              change("stop_on_warning", event.target.checked)
            }
          />
          Stop paid actions when DealMachine reports a warning
        </label>
      </div>
      <div className="pr-actions">
        <button className="button">Save Limits</button>
        <button
          className="button secondary"
          type="button"
          onClick={() => setDraft({ ...summary.settings.limits })}
        >
          Reset Changes
        </button>
      </div>
    </form>
  );
}
function CreditLimitField({
  label,
  value,
  onChange,
  min = 0,
  max = 10000000,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        required
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
function Categories({
  summary,
  reload,
  setError,
}: {
  summary: Summary;
  reload: () => Promise<void>;
  setError: (value: string) => void;
}) {
  const ready = summary.categories.filter(
    (category) => category.support_status === "available",
  ).length;
  return (
    <details className="platform-card dm-categories">
      <summary>
        Category mappings ({ready} of {summary.categories.length} ready)
      </summary>
      <p>
        First click <strong>Load DealMachine Filters</strong> above. Then open a
        category to review its mapping. Bulk search stays unavailable until a
        suitable DealMachine filter is confirmed.
      </p>
      <div>
        {summary.categories.map((category) => (
          <CategoryEditor
            key={category.key}
            category={category}
            reload={reload}
            setError={setError}
          />
        ))}
      </div>
    </details>
  );
}
function CategoryEditor({
  category,
  reload,
  setError,
}: {
  category: Summary["categories"][number];
  reload: () => Promise<void>;
  setError: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [enabled, setEnabled] = useState(category.enabled);
  const [draft, setDraft] = useState(() =>
    JSON.stringify(
      { ...category.configuration, support_status: category.support_status },
      null,
      2,
    ),
  );
  async function save() {
    try {
      await api.saveCategory(category.key, {
        ...(JSON.parse(draft) as Json),
        enabled,
      });
      await reload();
      setOpen(false);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Category update failed.",
      );
    }
  }
  const supportLabel =
    category.support_status === "available"
      ? "Ready"
      : category.support_status === "unsupported"
        ? "Unavailable"
        : "Needs mapping";
  return (
    <article className={open ? "open" : ""}>
      <div className="dm-category-open">
        <label className="pr-check">
          <input
            type="checkbox"
            checked={enabled}
            disabled={category.support_status !== "available"}
            onChange={(event) => setEnabled(event.target.checked)}
          />{" "}
          Include in Bulk Search
        </label>
        <strong>{category.label}</strong>
        <button onClick={() => setOpen(!open)}>
          <span
            className={`platform-status ${category.support_status === "available" ? "on" : "neutral"}`}
          >
            {supportLabel}
          </span>
        </button>
      </div>
      <small>
        {category.last_result_count} last · {category.new_match_count} new ·{" "}
        {category.changed_record_count} changed · {category.credits_used}{" "}
        credits
      </small>
      {open && (
        <>
          <label className="dm-editor-label">
            Filter mapping, locations, output fields, row limit and sorting
            <textarea
              className="dm-editor"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              spellCheck={false}
            />
          </label>
          <button className="button" onClick={() => void save()}>
            Save Configuration
          </button>
        </>
      )}
    </article>
  );
}
function Result({
  value,
  view,
  setView,
}: {
  value: unknown;
  view: "table" | "json";
  setView: (value: "table" | "json") => void;
}) {
  const rows = extractRows(value);
  return (
    <div className="dm-result">
      <div className="dm-result-toolbar">
        <strong>Result</strong>
        <button
          onClick={() => setView("table")}
          className={view === "table" ? "active" : ""}
        >
          Table
        </button>
        <button
          onClick={() => setView("json")}
          className={view === "json" ? "active" : ""}
        >
          Raw JSON
        </button>
        <button
          onClick={() =>
            void navigator.clipboard.writeText(JSON.stringify(value, null, 2))
          }
        >
          Copy JSON
        </button>
      </div>
      {view === "json" || !rows.length ? (
        <pre>{JSON.stringify(value, null, 2)}</pre>
      ) : (
        <History rows={rows} />
      )}
    </div>
  );
}
function History({ rows }: { rows: Json[] }) {
  if (!rows.length)
    return (
      <div className="platform-card pr-empty">No execution records yet.</div>
    );
  const keys = Object.keys(rows[0])
    .filter((key) => !key.endsWith("_json"))
    .slice(0, 8);
  return (
    <div className="platform-table-wrap dm-table">
      <table>
        <thead>
          <tr>
            {keys.map((key) => (
              <th key={key}>{key.replaceAll("_", " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.id ?? index)}>
              {keys.map((key) => (
                <td key={key}>{format(row[key])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function extractRows(value: unknown): Json[] {
  if (!value || typeof value !== "object") return [];
  const root = value as Json;
  const direct = root.items ?? root.data;
  if (Array.isArray(direct))
    return direct.filter(
      (item): item is Json => !!item && typeof item === "object",
    );
  if (
    direct &&
    typeof direct === "object" &&
    Array.isArray((direct as Json).data)
  )
    return (direct as Json).data as Json[];
  return [];
}
function format(value: unknown) {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
function downloadJson(value: unknown, name: string) {
  if (!value) return;
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}
