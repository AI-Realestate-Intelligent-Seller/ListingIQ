"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Toast } from "@/components/toast/toast";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { requestJson } from "@/lib/api/http-client";
import { RealEstateProfile } from "@/features/leads/components/lead-detail-drawer";
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

const root = "/integrations/batchdata";
const products = ["Property Search", "Contact Enrichment"] as const;
const batchDataNavigationSections = [
  "Connection",
  "Data",
  "Usage & Limits",
  "Activity",
  "History",
] as const satisfies readonly IntegrationSection[];
type Tab =
  | "Connection"
  | "PAYG Billing"
  | "Product Data"
  | "Properties"
  | "Monitoring"
  | "History"
  | "Saved Files";
type Product = (typeof products)[number];
const batchDataSections: Record<
  IntegrationSection,
  readonly IntegrationSubtab[]
> = {
  Connection: [{ id: "Connection", label: "Connection" }],
  Configuration: [{ id: "Monitoring", label: "Configuration" }],
  Data: [
    ...products.map((product) => ({ id: product, label: product })),
    { id: "Properties", label: "Saved Properties" },
  ],
  "Usage & Limits": [{ id: "PAYG Billing", label: "Usage & Limits" }],
  Activity: [{ id: "History", label: "Runs & Events" }],
  History: [{ id: "Saved Files", label: "Saved Files" }],
};
const batchDataActiveSubtab = (tab: Tab, product: Product) =>
  tab === "Product Data" ? product : tab;
const batchDataSectionFor = (tab: Tab, product: Product) => {
  const active = batchDataActiveSubtab(tab, product);
  return (Object.entries(batchDataSections).find(([, subtabs]) =>
    subtabs.some((subtab) => subtab.id === active),
  )?.[0] || "Connection") as IntegrationSection;
};

type Config = {
  configurationVersion: number;
  enabled: boolean;
  billingMode: "pay_as_you_go";
  monthlySpendCap: number;
  skipTraceSpendCap: number;
  monthlySkipTraceLimit: number;
  basicPropertyUnitCost: number;
  quickListUnitCost: number;
  listingUnitCost: number;
  contactEnrichmentUnitCost: number;
  allowOverage: false;
  rowsPerCategory: number;
  selectedCategories: string[];
  locations: string[];
  combination: "AND" | "OR" | null;
  propertySearchEnabled: boolean;
  contactEnrichmentEnabled: boolean;
  publicWebhookUrl: string;
  monitorNewMatchUnitCost: number | null;
  monitorUpdateUnitCost: number | null;
  monitorMonthlyFixedCost: number | null;
  monitorMonthlySpendCap: number | null;
};
type Summary = {
  api_mode?: string;
  status: string;
  enabled: boolean;
  connection_status: string;
  token_configured: boolean;
  webhook_secret_configured: boolean;
  last_successful_call: string | null;
  last_webhook: string | null;
  last_error: string | null;
  config: Config;
  categories: { key: string; label: string; supported: boolean }[];
  usage: {
    monthly_spend: number;
    monthly_cap: number;
    skip_trace_spend: number;
    skip_trace_cap: number;
    skip_trace_matches: number;
    skip_trace_limit: number;
    allow_overage: false;
  };
  metrics: { properties: number; runs: number; webhooks: number };
  monitoring_ready: boolean;
  monitoring_status: string;
};
type Run = {
  properties?: Record<string, unknown>[];
  provider_calls?: {
    request: unknown;
    response: unknown;
    request_id: string;
    product: string;
  }[];
  id: string;
  status: string;
  configuration_version: number;
  call_plan: {
    property_search_calls: number;
    maximum_skip_trace_calls: number;
    maximum_returned_rows: number;
    estimated_cost: number;
    preview_hash?: string;
    calls: { category: string; endpoint: string; request: unknown }[];
  };
  estimated_cost: number;
  actual_cost: number;
  returned_records: number;
  unique_properties: number;
  duplicate_properties: number;
};
type Records = {
  items: Record<string, unknown>[];
  total: number;
  offset: number;
  limit: number;
};

async function api<T>(
  path = "",
  method: "GET" | "POST" | "PUT" = "GET",
  payload?: unknown,
) {
  const session = readAuthSession();
  if (!session || session.user.role !== "platform_admin")
    throw new Error("Internal administrator access is required.");
  return requestJson<T>(root + path, {
    method,
    payload,
    accessToken: session.access_token,
  });
}
const when = (value: string | null) =>
  value ? new Date(value).toLocaleString() : "—";
const money = (value: number) => `$${Number(value).toFixed(2)}`;

export function BatchDataView({ detail = false }: { detail?: boolean }) {
  const [data, setData] = useState<Summary | null>(null);
  const [draft, setDraft] = useState<Config | null>(null);
  const [tab, setTab] = useState<Tab>("Connection");
  const [product, setProduct] = useState<Product>("Property Search");
  const [runs, setRuns] = useState<Partial<Record<Product, Run>>>({});
  const run = runs[product] || null;
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const reload = useCallback(async (reset = false) => {
    const value = await api<Summary>();
    setData(value);
    setDraft((current) => (reset || !current ? value.config : current));
    setRefreshKey((value) => value + 1);
  }, []);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial API hydration
    void reload().catch((reason) => setError(reason.message));
  }, [reload]);
  async function work(name: string, action: () => Promise<void>) {
    if (busy) return;
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
      setBusy("");
    }
  }
  function change<K extends keyof Config>(key: K, value: Config[K]) {
    setDraft((current) => (current ? { ...current, [key]: value } : current));
    setRuns((current) => ({ ...current, [product]: undefined }));
  }
  async function save() {
    if (!draft) return;
    await work("Save draft", async () => {
      const value = await api<Summary>("/config", "PUT", draft);
      setData(value);
      setDraft(value.config);
      setRuns((current) => ({ ...current, [product]: undefined }));
    });
  }
  async function action(
    name: string,
    endpoint: string,
    payload: Record<string, unknown> = {},
  ) {
    await work(name, async () => {
      const value = await api<Run>(endpoint, "POST", payload);
      if (value?.call_plan)
        setRuns((current) => ({ ...current, [product]: value }));
    });
  }
  function toggle() {
    if (!data || !draft) return;
    void work(
      data.enabled ? "Disable BatchData" : "Enable BatchData",
      async () => {
        const value = await api<Summary>("/config", "PUT", {
          ...draft,
          enabled: !data.enabled,
        });
        setData(value);
        setDraft(value.config);
      },
    );
  }
  if (!data || !draft) {
    if (error && !detail)
      return (
        <ProviderIntegrationCard
          provider="batchdata"
          name="BatchData"
          href="/platform-admin/integrations/batchdata"
          status="Unavailable"
          statusTone="off"
          description="PAYG property search, deduplicated owner enrichment, monitoring and immutable provider records."
          metrics={[
            { label: "Connection", value: "Unavailable" },
            { label: "Credential", value: "Unknown" },
            { label: "PAYG usage", value: "—" },
            { label: "Properties", value: "—" },
            { label: "Last activity", value: "—" },
            { label: "Last error", value: "Request failed" },
          ]}
          notice={error}
          noticeTone="error"
        >
          <button className="provider-card-retry" onClick={() => void reload()}>
            Retry
          </button>
        </ProviderIntegrationCard>
      );
    return (
      <section className="pr-workspace" aria-live="polite">
        <p className={error ? "platform-error" : undefined}>
          {error || "Loading BatchData…"}
        </p>
        <button className="button secondary" onClick={() => void reload()}>
          Refresh
        </button>
      </section>
    );
  }
  const cardStatus =
    data.status === "Error"
      ? "Error"
      : data.connection_status === "connected"
        ? "Ready"
        : data.token_configured
          ? "Needs testing"
          : "Needs setup";
  const statusTone =
    cardStatus === "Ready" ? "on" : cardStatus === "Error" ? "off" : "neutral";
  if (!detail)
    return (
      <ProviderIntegrationCard
        provider="batchdata"
        name="BatchData"
        href="/platform-admin/integrations/batchdata"
        status={cardStatus}
        statusTone={statusTone}
        description="PAYG property search, deduplicated owner enrichment, monitoring and immutable provider records."
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
            label: "PAYG usage",
            value: `${money(data.usage.monthly_spend)} / ${money(data.usage.monthly_cap)}`,
          },
          {
            label: "Properties",
            value: data.metrics.properties.toLocaleString(),
          },
          { label: "Last activity", value: when(data.last_successful_call) },
          { label: "Last error", value: data.last_error || "None" },
        ]}
        notice={
          data.token_configured
            ? "Configuration is ready for controlled PAYG operations."
            : "Needs setup: API token missing from the server’s .env."
        }
      >
        {error && (
          <Toast message={error} tone="error" onDone={() => setError("")} />
        )}
      </ProviderIntegrationCard>
    );
  return (
    <div className="pr-workspace bd-workspace">
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
            <ProviderIcon provider="batchdata" /> BatchData
          </h2>
          <span className={`platform-status ${statusTone}`}>{cardStatus}</span>
        </div>
        <div className="pr-actions">
          <button
            className="button secondary"
            disabled={!!busy}
            onClick={toggle}
          >
            {data.enabled ? "Pause Provider Access" : "Allow Provider Access"}
          </button>
          <button
            className="button secondary"
            disabled={!!busy}
            onClick={() => void reload()}
          >
            Refresh
          </button>
        </div>
      </div>
      <IntegrationNavigation
        provider="BatchData"
        sections={batchDataNavigationSections}
        activeSection={batchDataSectionFor(tab, product)}
        onSectionChange={(section) => {
          const destination = batchDataSections[section][0].id;
          if ((products as readonly string[]).includes(destination)) {
            setProduct(destination as Product);
            setTab("Product Data");
          } else {
            setTab(destination as Tab);
          }
        }}
        subtabs={batchDataSections[batchDataSectionFor(tab, product)]}
        activeSubtab={batchDataActiveSubtab(tab, product)}
        onSubtabChange={(subtab) => {
          if ((products as readonly string[]).includes(subtab)) {
            setProduct(subtab as Product);
            setTab("Product Data");
          } else {
            setTab(subtab as Tab);
          }
        }}
      />
      {tab === "Connection" && (
        <Connection
          data={data}
          busy={busy}
          test={() => action("Test connection", "/connection/test")}
        />
      )}
      {tab === "PAYG Billing" && (
        <Billing
          data={data}
          draft={draft}
          change={change}
          save={save}
          busy={busy}
        />
      )}
      {tab === "Product Data" &&
        (product === "Property Search" ? (
          <ProductData
            data={data}
            draft={draft}
            change={change}
            product={product}
            run={run}
            busy={busy}
            save={save}
            action={action}
            clearRun={() =>
              setRuns((current) => ({
                ...current,
                "Property Search": undefined,
              }))
            }
            reviewSaved={() => {
              setTab("Properties");
            }}
          />
        ) : (
          <SelectedPropertyStage
            key={product}
            data={data}
            draft={draft}
            change={change}
            product={product}
            run={run}
            busy={busy}
            save={save}
            action={action}
            clearRun={() =>
              setRuns((current) => ({
                ...current,
                "Contact Enrichment": undefined,
              }))
            }
          />
        ))}
      {tab === "Properties" && (
        <>
          <div className="platform-metrics">
            <article>
              <span>Saved properties</span>
              <strong>{data.metrics.properties}</strong>
            </article>
            <article>
              <span>Primary identity</span>
              <strong>BatchData _id</strong>
            </article>
            <article>
              <span>Skip-trace overage</span>
              <strong>Never allowed</strong>
            </article>
          </div>
          <div className="pr-actions">
            <Link
              className="button"
              href={`/operations/integrations/distribution?mode=${data.api_mode === "sandbox" ? "sandbox" : "live"}`}
            >
              Distribute to Brokerages
            </Link>
            <span>
              Combine saved properties in Integration Data, then allocate them
              to any brokerage.
            </span>
          </div>
          <RecordTable resource="properties" refreshKey={refreshKey} />
          <h3>Category memberships</h3>
          <RecordTable resource="memberships" refreshKey={refreshKey} />
        </>
      )}
      {data.api_mode === "sandbox" && (
        <p className="platform-notice">
          BatchData sandbox token active. Use the normal product fields and Run
          Endpoint workflow. Returned records are provider mock data; sandbox
          responses may not reflect your filters. Actual spend is $0; estimates
          show the configured pricing.
        </p>
      )}
      {tab === "Monitoring" && (
        <Monitoring
          data={data}
          draft={draft}
          change={change}
          save={save}
          busy={busy}
          validate={() => action("Validate monitoring", "/monitoring/validate")}
        />
      )}
      {tab === "History" && <History refreshKey={refreshKey} />}
      {tab === "Saved Files" && (
        <>
          <div className="platform-notice">
            <strong>Credential-safe provider archive</strong>
            <p>
              Configuration versions, call plans, redacted requests, immutable
              responses and exports are stored under
              storage/integrations/batchdata.
            </p>
          </div>
          <RecordTable resource="saved-files" refreshKey={refreshKey} />
        </>
      )}
      {busy && <p role="status">{busy}…</p>}
    </div>
  );
}

function Connection({
  data,
  busy,
  test,
}: {
  data: Summary;
  busy: string;
  test: () => void;
}) {
  const checks = [
    [
      data.token_configured,
      "API token",
      data.token_configured
        ? "Configured in the API environment."
        : "Set BATCHDATA_API_TOKEN on the API server.",
    ],
    [
      data.connection_status === "connected" ||
        data.connection_status === "sandbox_verified",
      data.api_mode === "sandbox"
        ? "Sandbox connection verification"
        : "Live connection verification",
      data.connection_status === "sandbox_verified"
        ? "Verified by a successful BatchData sandbox search."
        : data.connection_status === "connected"
          ? "Verified by a successful approved call."
          : "Deferred until an approved execution; setup checks never create a billable call.",
    ],
    [
      data.webhook_secret_configured,
      "Webhook signing secret",
      data.webhook_secret_configured
        ? "Configured."
        : "Set a 32+ character BATCHDATA_WEBHOOK_SECRET.",
    ],
  ] as const;
  return (
    <section className="platform-card pr-connection-card">
      <div>
        <span>Connection status</span>
        <strong>{data.connection_status.replaceAll("_", " ")}</strong>
      </div>
      <ul className="pr-checklist">
        {checks.map(([ok, title, copy]) => (
          <li key={title} className={ok ? "ok" : "pending"}>
            <span className="pr-checklist-mark">{ok ? "✓" : "!"}</span>
            <div>
              <strong>{title}</strong>
              <p>{copy}</p>
            </div>
          </li>
        ))}
      </ul>
      <p>
        The token is read server-side and is never returned to the browser or
        saved in request archives. This check is local and cannot incur PAYG
        usage.
      </p>
      <IntegrationActionGuidance kind="free">
        Testing the connection checks server-side setup without running a paid
        property search or contact enrichment.
      </IntegrationActionGuidance>
      <div className="pr-actions">
        <button
          className="button"
          disabled={!!busy || !data.token_configured}
          onClick={test}
        >
          Test Connection
        </button>
      </div>
    </section>
  );
}

function Billing({
  data,
  draft,
  change,
  save,
  busy,
}: {
  data: Summary;
  draft: Config;
  change: <K extends keyof Config>(key: K, value: Config[K]) => void;
  save: () => void;
  busy: string;
}) {
  const fields: [keyof Config, string][] = [
    ["monthlySpendCap", "Monthly spend cap"],
    ["skipTraceSpendCap", "Skip-trace spend cap"],
    ["monthlySkipTraceLimit", "Monthly skip-trace matches"],
    ["quickListUnitCost", "Quick List / result"],
    ["basicPropertyUnitCost", "Basic property / result"],
    ["listingUnitCost", "Listing data / result"],
    ["contactEnrichmentUnitCost", "Contact enrichment / match"],
  ];
  return (
    <section className="platform-card pr-form">
      <h3>PAYG limits and account-specific rates</h3>
      <p>
        Costs are estimated and reserved before each run, then reconciled
        against returned or matched rows. Paid work is blocked at 100%; overage
        cannot be enabled.
      </p>
      <div className="bd-usage-bars">
        <UsageBar
          label="Monthly spend"
          value={data.usage.monthly_spend}
          cap={data.usage.monthly_cap}
        />
        <UsageBar
          label="Skip trace"
          value={data.usage.skip_trace_spend}
          cap={data.usage.skip_trace_cap}
        />
      </div>
      <div className="pr-fields">
        {fields.map(([key, title]) => (
          <NumberField
            key={key}
            title={title}
            value={draft[key] as number}
            onChange={(value) => change(key, value as never)}
          />
        ))}
        <label>
          Billing mode
          <input value="Pay as you go" disabled />
        </label>
        <label className="pr-check">
          <input type="checkbox" checked={false} disabled /> Overage disabled
          permanently
        </label>
      </div>
      <div className="pr-actions">
        <button
          className="button"
          disabled={!!busy}
          onClick={() => void save()}
        >
          Save Limits
        </button>
      </div>
    </section>
  );
}

function ProductData({
  data,
  draft,
  change,
  product,
  run,
  busy,
  save,
  action,
  reviewSaved,
}: {
  data: Summary;
  draft: Config;
  change: <K extends keyof Config>(key: K, value: Config[K]) => void;
  product: Product;
  run: Run | null;
  busy: string;
  save: () => void;
  action: (
    name: string,
    endpoint: string,
    payload?: Record<string, unknown>,
  ) => Promise<void>;
  clearRun: () => void;
  reviewSaved: () => void;
}) {
  const definition = {
    "Property Search": [
      "propertySearchEnabled",
      "/api/v1/property/search",
      draft.basicPropertyUnitCost +
        draft.listingUnitCost +
        draft.quickListUnitCost,
      "/products/property-search",
    ],
    "Contact Enrichment": [
      "contactEnrichmentEnabled",
      "/api/v1/property/skip-trace",
      draft.contactEnrichmentUnitCost,
      "/products/contact-enrichment",
    ],
  }[product] as [keyof Config, string, number, string];
  const selected = new Set(draft.selectedCategories);
  const preview = useMemo(
    () => ({
      endpoint: definition[1],
      rowsPerCategory: draft.rowsPerCategory,
      locations: draft.locations,
      combination: draft.combination,
      selectedCategories: draft.selectedCategories,
      datasets: ["basic", "listing", "quicklist"],
      internalEndpoint: `/api/v1/integrations/batchdata${definition[3]}`,
    }),
    [definition, draft],
  );
  const requestPayload = (confirmed = false, reason = "") => ({
    selected_categories: draft.selectedCategories,
    locations: draft.locations,
    combination: draft.combination,
    rows_per_category: draft.rowsPerCategory,
    confirmed,
    reason,
  });
  const previewCall = () => {
    void action(`Preview ${product}`, definition[3], requestPayload());
  };
  const executeCall = () => {
    const reason = window.prompt(`Reason for running ${product}:`);
    if (
      reason &&
      window.confirm(
        `Run the dedicated ${product} endpoint? The backend will re-check limits and reserve up to ${money(run?.estimated_cost || 0)}.`,
      )
    ) {
      void action(
        `Run ${product}`,
        definition[3],
        requestPayload(true, reason),
      );
    }
  };
  return (
    <>
      <section className="platform-card pr-form">
        <div className="bd-product-head">
          <div>
            <h3>{product}</h3>
            <p>
              <code>{definition[1]}</code> · {money(definition[2])} per{" "}
              {product === "Contact Enrichment"
                ? "matched property"
                : "returned result"}
            </p>
            <p>
              Internal:{" "}
              <code>POST /api/v1/integrations/batchdata{definition[3]}</code>
            </p>
          </div>
          <label className="pr-check">
            <input
              type="checkbox"
              checked={Boolean(draft[definition[0]])}
              onChange={(event) =>
                change(definition[0], event.target.checked as never)
              }
            />{" "}
            Allow Product Access
          </label>
        </div>
        {product !== "Contact Enrichment" && (
          <>
            <div className="pr-fields">
              <label>
                Locations (separate with semicolons)
                <input
                  value={draft.locations.join("; ")}
                  onChange={(event) =>
                    change(
                      "locations",
                      event.target.value.split(";").map((item) => item.trim()),
                    )
                  }
                  placeholder="Chicago, IL; Austin, TX; 60601"
                />
              </label>
              <label>
                Combination (optional)
                <select
                  value={draft.combination ?? ""}
                  onChange={(event) =>
                    change(
                      "combination",
                      (event.target.value || null) as "AND" | "OR" | null,
                    )
                  }
                >
                  <option value="">None</option>
                  <option value="OR">OR</option>
                  <option value="AND">AND</option>
                </select>
                <small>
                  None or OR keeps every returned category page. AND keeps
                  only properties present in every returned category page.
                </small>
              </label>
              <NumberField
                title="Results per category"
                value={draft.rowsPerCategory}
                onChange={(value) => change("rowsPerCategory", value)}
              />
            </div>
            <div className="pr-categories">
              {data.categories.map((category) => (
                <label className="pr-check" key={category.key}>
                  <input
                    type="checkbox"
                    disabled={!category.supported}
                    checked={selected.has(category.key) && category.supported}
                    onChange={(event) =>
                      change(
                        "selectedCategories",
                        event.target.checked
                          ? [...draft.selectedCategories, category.key]
                          : draft.selectedCategories.filter(
                              (key) => key !== category.key,
                            ),
                      )
                    }
                  />{" "}
                  {category.label}
                  {!category.supported ? " — unsupported" : ""}
                </label>
              ))}
            </div>
          </>
        )}
        <details className="bd-json">
          <summary>Request JSON preview</summary>
          <pre>{JSON.stringify(run?.call_plan || preview, null, 2)}</pre>
        </details>
        <div className="platform-metrics bd-run-metrics">
          <article>
            <span>Planned calls</span>
            <strong>{run?.call_plan.property_search_calls ?? 0}</strong>
          </article>
          <article>
            <span>Maximum rows</span>
            <strong>
              {run?.call_plan.maximum_returned_rows ??
                draft.selectedCategories.length * draft.rowsPerCategory}
            </strong>
          </article>
          <article>
            <span>Estimated cost</span>
            <strong>{money(run?.estimated_cost ?? 0)}</strong>
          </article>
          <article>
            <span>Status</span>
            <strong>{run?.status ?? "Not planned"}</strong>
          </article>
        </div>
        <IntegrationActionGuidance
          kind={data.api_mode === "sandbox" ? "free" : "paid"}
        >
          Preview is free. Run uses only this product endpoint after
          confirmation; the backend rechecks the product limit and spending cap.
        </IntegrationActionGuidance>
        <div className="pr-actions bd-workflow">
          <button
            className="button secondary"
            disabled={!!busy}
            onClick={() => void save()}
          >
            Save Configuration
          </button>
          <button
            className="button secondary"
            disabled={!!busy}
            onClick={previewCall}
          >
            Preview Endpoint
          </button>
          <button
            className="button"
            disabled={!!busy || run?.status !== "Preview" || !data.enabled}
            onClick={executeCall}
          >
            Run Endpoint
          </button>
        </div>
      </section>
      {run?.status === "Completed" && (
        <section className="platform-card">
          <h3>
            {data.api_mode === "sandbox"
              ? "Returned mock data"
              : "Returned provider data"}
          </h3>
          <p>
            {run.returned_records} results returned · {run.unique_properties}{" "}
            unique properties · {run.duplicate_properties} duplicates. Saved
            properties and Activity have been refreshed.
          </p>
          <button className="button secondary" onClick={reviewSaved}>
            Review Saved Properties
          </button>
          {!!run.properties?.length && <PropertyTable rows={run.properties} />}
          {run.provider_calls?.map((call, index) => (
            <details key={index} className="bd-json">
              <summary>
                {call.product} · Request {call.request_id}
              </summary>
              <strong>Sent request</strong>
              <pre>{JSON.stringify(call.request, null, 2)}</pre>
              <strong>Provider response</strong>
              <pre>{JSON.stringify(call.response, null, 2)}</pre>
            </details>
          ))}
        </section>
      )}
    </>
  );
}

function SelectedPropertyStage({
  data,
  draft,
  product,
  run,
  busy,
  action,
  clearRun,
  change,
  save,
}: {
  data: Summary;
  draft: Config;
  product: Product;
  run: Run | null;
  busy: string;
  action: (
    name: string,
    endpoint: string,
    payload?: Record<string, unknown>,
  ) => Promise<void>;
  clearRun: () => void;
  change: <K extends keyof Config>(key: K, value: Config[K]) => void;
  save: () => void;
}) {
  const [records, setRecords] = useState<Records | null>(null);
  const [selectedIds, setSelectedIds] = useState<number[]>([]);
  const [selectingAll, setSelectingAll] = useState(false);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const definitions = [
    "contactEnrichmentEnabled",
    "/products/contact-enrichment",
  ] as [keyof Config, string];
  useEffect(() => {
    let active = true;
    void api<Records>(`/properties?current_mode=true&offset=${offset}`)
      .then((value) => {
        if (active) setRecords(value);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [offset, run?.id, run?.status]);
  const rows = (records?.items || []).map((row) => {
    let status = (row.stages as Record<string, { status?: string }> | undefined)
      ?.contacts?.status;
    if (
      !status &&
      ["matched", "no_match"].includes(String(row.skiptrace_status))
    )
      status = "completed";
    return {
      ...(row.table_data as Record<string, unknown>),
      property_id: (row.table_data as Record<string, unknown>).property_id,
      address: (row.table_data as Record<string, unknown>).address,
      record_id: row.id,
      selectable: row.property_search_saved && !status,
      stage_status: status,
    };
  });
  const alreadyEnriched = rows.filter(
    (row) => row.stage_status === "completed" || row.stage_status === "no_match",
  ).length;
  const payload = (confirmed = false) => ({
    property_ids: selectedIds,
    confirmed,
    reason: "",
    preview_hash: confirmed ? run?.call_plan.preview_hash : "",
  });
  const selectAll = async () => {
    setSelectingAll(true);
    setError("");
    clearRun();
    try {
      const result = await api<{ property_ids: number[] }>(
        "/properties/selectable-ids",
      );
      setSelectedIds(result.property_ids);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not select saved properties",
      );
    } finally {
      setSelectingAll(false);
    }
  };
  const execute = () => {
    const reason = window.prompt(
      `Reason for retrieving contacts for ${selectedIds.length} selected properties:`,
    );
    if (
      reason &&
      window.confirm(
        `Retrieve contacts for ${selectedIds.length} saved properties? Estimated maximum ${money(run?.estimated_cost || 0)}.${data.api_mode === "sandbox" ? " Your sandbox token returns mock data with $0 actual spend." : ""}`,
      )
    )
      void action("Get Contacts", definitions[1], {
        ...payload(true),
        reason,
      }).then(() => setSelectedIds([]));
  };
  return (
    <section className="platform-card">
      <h3>{product}</h3>
      <p>
        Review saved Property Search records below. Viewing this table makes no
        provider request. Select only the properties you want to enrich with
        owner contacts.
      </p>
      <label className="pr-check">
        <input
          type="checkbox"
          checked={Boolean(draft[definitions[0]])}
          onChange={(event) =>
            change(definitions[0], event.target.checked as never)
          }
        />
        Allow Product Access
      </label>
      <button
        className="button secondary"
        disabled={!!busy}
        onClick={() => void save()}
      >
        Save Configuration
      </button>
      {error && <p className="platform-error">{error}</p>}
      <div className="pr-actions">
        <button
          className="button secondary"
          disabled={!!busy || selectingAll || !records?.total}
          onClick={() => void selectAll()}
        >
          {selectingAll ? "Selecting…" : "Select All"}
        </button>
        <button
          className="button secondary"
          disabled={!!busy || selectingAll || !selectedIds.length}
          onClick={() => {
            setSelectedIds([]);
            clearRun();
          }}
        >
          Clear
        </button>
        <span>Select All selects available properties across all pages.</span>
      </div>
      {!!alreadyEnriched && (
        <p className="platform-notice">
          {alreadyEnriched} saved {alreadyEnriched === 1 ? "property is" : "properties are"}{" "}
          already contact-enriched and shown below with selection disabled. Their saved
          contacts are reused, so BatchData is not charged again.
        </p>
      )}
      {!records ? (
        <p>Loading saved Property Search records…</p>
      ) : !rows.length ? (
        <p>No saved properties in this API mode. Run Property Search first.</p>
      ) : (
        <PropertyTable
          rows={rows}
          selectedIds={selectedIds}
          selectionDisabled={!!busy || selectingAll}
          toggle={(id) => {
            setSelectedIds((ids) =>
              ids.includes(id)
                ? ids.filter((value) => value !== id)
                : [...ids, id],
            );
            clearRun();
          }}
        />
      )}
      {records && (
        <div className="pr-actions">
          <button
            className="button secondary"
            disabled={!!busy || offset === 0}
            onClick={() => setOffset(Math.max(0, offset - 25))}
          >
            Previous
          </button>
          <span>{records.total} saved properties</span>
          <button
            className="button secondary"
            disabled={!!busy || offset + 25 >= records.total}
            onClick={() => setOffset(offset + 25)}
          >
            Next
          </button>
        </div>
      )}
      <p>
        {selectedIds.length} selected · Estimated maximum:{" "}
        {money(run?.estimated_cost || 0)} · Status:{" "}
        {run?.status || "Not previewed"}
      </p>
      {!!records?.items.some(
        (row) => (row.contact_rows as unknown[])?.length,
      ) && (
        <>
          <h3>Saved contacts</h3>
          <div className="platform-table-wrap bd-table">
            <table>
              <thead>
                <tr>
                  <th>Property ID</th>
                  <th>Name</th>
                  <th>Phones</th>
                  <th>Emails</th>
                </tr>
              </thead>
              <tbody>
                {records.items.flatMap((row) =>
                  ((row.contact_rows as Record<string, unknown>[]) || []).map(
                    (contact, index) => (
                      <tr key={`${row.id}-${index}`}>
                        <td>{formatCell(contact.property_id)}</td>
                        <td>{formatCell(contact.name)}</td>
                        <td>{formatCell(contact.phones)}</td>
                        <td>{formatCell(contact.emails)}</td>
                      </tr>
                    ),
                  ),
                )}
              </tbody>
            </table>
          </div>
        </>
      )}
      <div className="pr-actions">
        <button
          className="button secondary"
          disabled={!!busy || selectingAll || !selectedIds.length}
          onClick={() =>
            void action("Preview Contacts", definitions[1], payload())
          }
        >
          {"Preview Contacts"}
        </button>
        <button
          className="button"
          disabled={
            !!busy || selectingAll || !selectedIds.length || !data.enabled
          }
          onClick={() => {
            if (run?.status === "Preview") execute();
            else void action("Preview Contacts", definitions[1], payload());
          }}
        >
          Get Contacts
        </button>
      </div>
      {!!selectedIds.length && run?.status !== "Preview" && (
        <p>
          Get Contacts first previews the selection and cost. Click again after
          reviewing the preview to confirm retrieval.
        </p>
      )}
      {!data.enabled && (
        <p>Enable BatchData in Connection to retrieve selected properties.</p>
      )}
      {run && (
        <details className="bd-json">
          <summary>Selected-property call plan</summary>
          <pre>{JSON.stringify(run.call_plan, null, 2)}</pre>
        </details>
      )}
      {run?.status === "Completed" && (
        <>
          <p role="status">
            Saved contact responses for {run.unique_properties} selected
            properties. Use View property to inspect saved stage data.
          </p>
          {run.provider_calls?.map((call, index) => (
            <details key={index} className="bd-json">
              <summary>
                {call.product} · Request {call.request_id}
              </summary>
              <pre>
                {JSON.stringify(
                  { request: call.request, response: call.response },
                  null,
                  2,
                )}
              </pre>
            </details>
          ))}
        </>
      )}
    </section>
  );
}

function Monitoring({
  data,
  draft,
  change,
  save,
  busy,
  validate,
}: {
  data: Summary;
  draft: Config;
  change: <K extends keyof Config>(key: K, value: Config[K]) => void;
  save: () => void;
  busy: string;
  validate: () => void;
}) {
  const fields: [keyof Config, string][] = [
    ["monitorNewMatchUnitCost", "New match unit cost"],
    ["monitorUpdateUnitCost", "Update unit cost"],
    ["monitorMonthlyFixedCost", "Monthly fixed cost"],
    ["monitorMonthlySpendCap", "Monitoring monthly cap"],
  ];
  return (
    <section className="platform-card pr-form">
      <div
        className={`pr-ready-banner ${data.monitoring_ready ? "ok" : "pending"}`}
      >
        {data.monitoring_status}
      </div>
      <p>
        No subscription calls can execute until every price and the dedicated
        cap are confirmed. Webhooks continue to be received and saved when paid
        calls are blocked.
      </p>
      <div className="pr-fields">
        {fields.map(([key, title]) => (
          <NullableNumberField
            key={key}
            title={title}
            value={draft[key] as number | null}
            onChange={(value) => change(key, value as never)}
          />
        ))}
        <label>
          Public HTTPS webhook URL
          <input
            type="url"
            value={draft.publicWebhookUrl}
            onChange={(event) => change("publicWebhookUrl", event.target.value)}
            placeholder="https://your-tunnel.example/api/v1/webhooks/batchdata"
          />
        </label>
      </div>
      <div className="platform-notice">
        <strong>Recommended monitor plan</strong>
        <p>
          21 separate category monitors for clearest tracking, or seven groups
          of three. Localhost requires a public HTTPS tunnel.
        </p>
      </div>
      <IntegrationActionGuidance kind="free">
        Saving this configuration and validating monitoring do not run paid
        property searches or contact enrichment.
      </IntegrationActionGuidance>
      <div className="pr-actions">
        <button
          className="button secondary"
          disabled={!!busy}
          onClick={() => void save()}
        >
          Save Configuration
        </button>
        <button
          className="button"
          disabled={!!busy || !data.monitoring_ready}
          onClick={validate}
        >
          Validate Monitoring
        </button>
      </div>
    </section>
  );
}

function History({ refreshKey }: { refreshKey: number }) {
  const [resource, setResource] = useState("runs");
  return (
    <>
      <nav className="bd-subtabs" aria-label="BatchData activity records">
        {[
          ["runs", "Runs"],
          ["api-calls", "Provider Calls"],
          ["webhooks", "Webhook Events"],
          ["audit", "Admin Actions"],
        ].map(([key, label]) => (
          <button
            key={key}
            className={resource === key ? "active" : ""}
            onClick={() => setResource(key)}
          >
            {label}
          </button>
        ))}
      </nav>
      <RecordTable key={resource} resource={resource} refreshKey={refreshKey} />
    </>
  );
}

function RecordTable({
  resource,
  refreshKey,
}: {
  resource: string;
  refreshKey: number;
}) {
  const [records, setRecords] = useState<Records | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [file, setFile] = useState<{
    name: string;
    content: string;
    properties?: Record<string, unknown>[];
  } | null>(null);
  useEffect(() => {
    void api<Records>(`/${resource}${offset ? `?offset=${offset}` : ""}`)
      .then(setRecords)
      .catch((reason) => setError(reason.message));
  }, [resource, refreshKey, offset]);
  if (error) return <p className="platform-error">{error}</p>;
  if (!records) return <p>Loading records…</p>;
  if (!records.items.length)
    return (
      <div className="platform-card pr-empty">
        No {resource.replaceAll("-", " ")} yet.
      </div>
    );
  const pagination = (
    <div className="pr-actions">
      <button
        className="button secondary"
        disabled={offset === 0}
        onClick={() => setOffset(Math.max(0, offset - 25))}
      >
        Previous
      </button>
      <span>
        {offset + 1}–{Math.min(offset + records.items.length, records.total)} of{" "}
        {records.total}
      </span>
      <button
        className="button secondary"
        disabled={offset + records.items.length >= records.total}
        onClick={() => setOffset(offset + 25)}
      >
        Next
      </button>
    </div>
  );
  if (resource === "properties")
    return (
      <>
        <PropertyTable
          rows={records.items.map((row) => ({
            ...(row.table_data as Record<string, unknown>),
            record_id: row.id,
            provider: row.provider,
          }))}
        />
        {pagination}
      </>
    );
  const keys = Object.keys(records.items[0])
    .filter(
      (key) =>
        ![
          "request_json",
          "response_json",
          "raw_payload",
          "call_plan",
          "selected_categories",
          "detail",
        ].includes(key),
    )
    .slice(0, 8);
  return (
    <>
      <div className="platform-table-wrap bd-table">
        <table>
          <thead>
            <tr>
              {keys.map((key) => (
                <th key={key}>{key.replaceAll("_", " ")}</th>
              ))}
              <th>Details</th>
            </tr>
          </thead>
          <tbody>
            {records.items.map((row, index) => (
              <tr key={String(row.id ?? index)}>
                {keys.map((key) => (
                  <td key={key}>{formatCell(row[key])}</td>
                ))}
                <td>
                  {resource === "saved-files" ? (
                    <button
                      className="button secondary"
                      onClick={() =>
                        void api<{
                          name: string;
                          content: string;
                          properties?: Record<string, unknown>[];
                        }>(`/saved-files/${row.id}/content`)
                          .then(setFile)
                          .catch((reason) => setError(reason.message))
                      }
                    >
                      View
                    </button>
                  ) : row.request_json || row.response_json || row.call_plan ? (
                    <details className="bd-json">
                      <summary>Request and response</summary>
                      <pre>
                        {JSON.stringify(
                          {
                            mode: (
                              row.call_plan as { mode?: string } | undefined
                            )?.mode,
                            plan: row.call_plan,
                            request: row.request_json,
                            response: row.response_json,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  ) : (
                    "—"
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {file && (
        <section className="platform-card">
          <h3>{file.name}</h3>
          <button className="button secondary" onClick={() => setFile(null)}>
            Close file
          </button>
          {!!file.properties?.length && (
            <PropertyTable rows={file.properties} />
          )}
          <details className="bd-json">
            <summary>File contents</summary>
            <pre>{file.content}</pre>
          </details>
          <button
            className="button secondary"
            onClick={() => {
              const url = URL.createObjectURL(
                new Blob([file.content], {
                  type: file.name.endsWith(".csv")
                    ? "text/csv"
                    : "application/json",
                }),
              );
              const link = document.createElement("a");
              link.href = url;
              link.download = file.name;
              link.click();
              URL.revokeObjectURL(url);
            }}
          >
            Download file
          </button>
        </section>
      )}
      {pagination}
    </>
  );
}

export function PropertyTable({
  rows,
  selectedIds,
  toggle,
  selectionDisabled,
  loadDetail,
}: {
  rows: Record<string, unknown>[];
  selectedIds?: number[];
  toggle?: (id: number) => void;
  selectionDisabled?: boolean;
  loadDetail?: (id: number) => Promise<unknown>;
}) {
  const [detail, setDetail] = useState<unknown>(null);
  const [error, setError] = useState("");
  const columns = [
    ["property_id", "Property ID"],
    ["address", "Address"],
    ["owner", "Owner"],
    ["property_type", "Type"],
    ["beds", "Beds"],
    ["baths", "Baths"],
    ["area_sqft", "Area (sq ft)"],
    ["estimated_value", "Estimated value"],
    ["equity_percent", "Equity %"],
    ["listing_status", "Listing status"],
    ["listing_price", "Listing price"],
  ];
  if (rows.some((row) => typeof row.distribution_issue === "string")) {
    columns.push(["distribution_issue", "Issue"]);
  }
  return (
    <>
      <div className="platform-table-wrap bd-table">
        <table>
          <thead>
            <tr>
              {toggle && <th>Select</th>}
              {columns.map(([key, label]) => (
                <th key={key}>{label}</th>
              ))}
              {rows.some((row) => row.record_id) && <th>Details</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={String(row.record_id || row.property_id || index)}>
                {toggle && (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select property ${row.property_id}`}
                      checked={
                        selectedIds?.includes(Number(row.record_id)) || false
                      }
                      disabled={selectionDisabled || !row.selectable}
                      onChange={() => toggle(Number(row.record_id))}
                    />
                  </td>
                )}
                {columns.map(([key]) => (
                  <td key={key}>{formatCell(row[key])}</td>
                ))}
                {rows.some((row) => row.record_id) && (
                  <td>
                    {toggle && (
                      <span>{String(row.stage_status || "Not requested")}</span>
                    )}
                    <button
                      className="button secondary"
                      onClick={() =>
                        void (
                          loadDetail
                            ? loadDetail(Number(row.record_id))
                            : api<{ data: unknown }>(
                                `/properties/${row.record_id}`,
                              )
                        )
                          .then((value) => setDetail(value))
                          .catch((reason) => setError(reason.message))
                      }
                    >
                      View property
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {error && <p className="platform-error">{error}</p>}
      {detail !== null && (
        <PropertyDetailsPanel detail={detail} close={() => setDetail(null)} />
      )}
    </>
  );
}
function PropertyDetailsPanel({
  detail,
  close,
}: {
  detail: unknown;
  close: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const wrapper = detail !== null && typeof detail === "object" && !Array.isArray(detail)
    ? detail as Record<string, unknown>
    : {};
  const saved = wrapper.data !== null && typeof wrapper.data === "object" && !Array.isArray(wrapper.data)
    ? wrapper.data as Record<string, unknown>
    : {};
  const stages = wrapper.stages !== null && typeof wrapper.stages === "object" && !Array.isArray(wrapper.stages)
    ? wrapper.stages as Record<string, unknown>
    : {};
  const address = saved.address !== null && typeof saved.address === "object" && !Array.isArray(saved.address)
    ? saved.address as Record<string, unknown>
    : {};
  const owner = saved.owner !== null && typeof saved.owner === "object" && !Array.isArray(saved.owner)
    ? saved.owner as Record<string, unknown>
    : {};
  const addressLabel = String(address.formatted || [address.street, address.city, address.state, address.zip].filter(Boolean).join(", ") || "Property details");
  const ownerLabel = String(owner.fullName || owner.name || "Owner not on file");
  const listing = saved.listing !== null && typeof saved.listing === "object" && !Array.isArray(saved.listing)
    ? saved.listing as Record<string, unknown>
    : {};
  const quickLists = saved.quickLists !== null && typeof saved.quickLists === "object" && !Array.isArray(saved.quickLists)
    ? saved.quickLists as Record<string, unknown>
    : {};
  const hasReadableProfile = Object.keys(listing).length > 0
    || Object.values(quickLists).some((value) => value === true)
    || (typeof address.latitude === "number" && typeof address.longitude === "number");
  useEffect(() => {
    const panel = dialog.current;
    panel?.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      panel?.close();
      document.body.style.overflow = previousOverflow;
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className="bd-property-panel"
      aria-label="Property details"
      onCancel={close}
      onClick={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div className="bd-property-panel-header">
        <div><span>PROVIDER PROPERTY BRIEF</span><h3>{addressLabel}</h3><p>Owner: {ownerLabel}</p></div>
        <button type="button" className="sms-icon-button" aria-label="Close property" onClick={close}>✕</button>
      </div>
      <div className="bd-property-panel-content">
        <div className="bd-property-profile">
          {hasReadableProfile
            ? <RealEstateProfile details={saved} latitude={null} longitude={null} leadSignals={[]} />
            : <PropertyFields value={saved} />}
          <AdminOwnerContactProfile saved={saved} stages={stages} />
        </div>
        {Object.keys(stages).length > 0 ? (
          <details className="bd-provider-evidence">
            <summary>Provider evidence &amp; stage data</summary>
            <PropertyFields value={stages} />
          </details>
        ) : null}
      </div>
    </dialog>
  );
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function arrayRecords(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(recordValue) : [];
}

function contactName(person: Record<string, unknown>): string {
  const name = recordValue(person.name);
  return String(
    person.fullName ||
    person.full ||
    name.full ||
    [person.first, person.middle, person.last].filter(Boolean).join(" ") ||
    [name.first, name.middle, name.last].filter(Boolean).join(" ") ||
    "Unnamed contact",
  );
}

function contactFlag(
  phone: Record<string, unknown>,
  keys: string[],
): string {
  const key = keys.find((candidate) => Object.hasOwn(phone, candidate));
  if (!key) return "Not provided";
  const value = phone[key];
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === "") return "Not provided";
  return String(value);
}

function AdminOwnerContactProfile({
  saved,
  stages,
}: {
  saved: Record<string, unknown>;
  stages: Record<string, unknown>;
}) {
  const owner = recordValue(saved.owner);
  const contactStage = recordValue(stages.contacts);
  const stageRows = Array.isArray(contactStage.data)
    ? arrayRecords(contactStage.data)
    : Object.keys(recordValue(contactStage.data)).length
      ? [recordValue(contactStage.data)]
      : [];
  const people = [
    ...stageRows.flatMap((row) => arrayRecords(row.persons)),
    ...arrayRecords(saved.persons),
    ...arrayRecords(saved.contacts),
    ...arrayRecords(owner.contacts),
  ].filter((person, index, rows) => {
    const fingerprint = JSON.stringify(person);
    return rows.findIndex((row) => JSON.stringify(row) === fingerprint) === index;
  });
  const status = String(contactStage.status || "not requested");
  const ownerNames = arrayRecords(owner.names);
  const mailing = recordValue(owner.mailingAddress);
  const mailingLabel = [mailing.street, mailing.city, mailing.state, mailing.zip]
    .filter(Boolean)
    .join(", ");

  return (
    <section className="bd-admin-contact-profile leads-detail-full" aria-label="Owner and contact intelligence">
      <div className="bd-admin-contact-heading">
        <div>
          <span>OWNER &amp; CONTACT INTELLIGENCE</span>
          <h4>{String(owner.fullName || owner.name || "Owner not on file")}</h4>
          <p>{mailingLabel || "Mailing address not on file"}</p>
        </div>
        <span className={`bd-contact-stage ${status.replaceAll("_", "-")}`}>
          Contact enrichment: {status.replaceAll("_", " ")}
        </span>
      </div>

      <dl className="leads-property-facts bd-owner-facts">
        <div><dt>Owner names</dt><dd>{ownerNames.map(contactName).join("; ") || String(owner.fullName || "—")}</dd></div>
        <div><dt>Mailing address</dt><dd>{mailingLabel || "—"}</dd></div>
        <div><dt>Provider request ID</dt><dd>{String(contactStage.request_id || "—")}</dd></div>
        <div><dt>Provider mode</dt><dd>{String(contactStage.mode || "—")}</dd></div>
      </dl>

      {people.length ? people.map((person, personIndex) => {
        const phones = [
          ...(Array.isArray(person.phoneNumbers) ? person.phoneNumbers : []),
          ...(Array.isArray(person.phones) ? person.phones : []),
        ];
        const emails = Array.isArray(person.emails) ? person.emails : [];
        return (
          <article className="bd-contact-person" key={`${contactName(person)}-${personIndex}`}>
            <div className="bd-contact-person-title">
              <div><span>CONTACT {personIndex + 1}</span><h5>{contactName(person)}</h5></div>
              <span>{phones.length} phone{phones.length === 1 ? "" : "s"} · {emails.length} email{emails.length === 1 ? "" : "s"}</span>
            </div>

            <div className="leads-data-table-wrap">
              <table className="leads-data-table bd-contact-table">
                <thead><tr><th>Phone</th><th>DNC</th><th>Type</th><th>Carrier</th><th>Provider details</th></tr></thead>
                <tbody>
                  {phones.length ? phones.map((value, phoneIndex) => {
                    const phone = typeof value === "string" ? { number: value } : recordValue(value);
                    const number = String(phone.number || phone.phone || phone.value || "—");
                    const dnc = contactFlag({ ...person, ...phone }, ["dnc", "isDnc", "isDNC", "doNotCall", "do_not_call"]);
                    return <tr key={`${number}-${phoneIndex}`}>
                      <td>{number === "—" ? number : <a href={`tel:${number}`}>{number}</a>}</td>
                      <td><span className={`bd-dnc-badge ${dnc.toLowerCase() === "yes" ? "blocked" : dnc.toLowerCase() === "no" ? "clear" : "unknown"}`}>{dnc}</span></td>
                      <td>{String(phone.type || phone.phoneType || "—")}</td>
                      <td>{String(phone.carrier || phone.carrierName || "—")}</td>
                      <td><details><summary>All fields</summary><PropertyFields value={phone} /></details></td>
                    </tr>;
                  }) : <tr><td colSpan={5}>No phone numbers returned by the provider.</td></tr>}
                </tbody>
              </table>
            </div>

            <div className="leads-data-table-wrap">
              <table className="leads-data-table compact bd-contact-table">
                <thead><tr><th>Email</th><th>Status / type</th><th>Provider details</th></tr></thead>
                <tbody>
                  {emails.length ? emails.map((value, emailIndex) => {
                    const email = typeof value === "string" ? { email: value } : recordValue(value);
                    const address = String(email.email || email.address || email.value || "—");
                    return <tr key={`${address}-${emailIndex}`}>
                      <td>{address === "—" ? address : <a href={`mailto:${address}`}>{address}</a>}</td>
                      <td>{String(email.status || email.type || email.emailType || "—")}</td>
                      <td><details><summary>All fields</summary><PropertyFields value={email} /></details></td>
                    </tr>;
                  }) : <tr><td colSpan={3}>No email addresses returned by the provider.</td></tr>}
                </tbody>
              </table>
            </div>

            <details className="bd-contact-raw">
              <summary>Complete provider contact profile</summary>
              <PropertyFields value={person} />
            </details>
          </article>
        );
      }) : (
        <div className="bd-no-contact-data">
          No enriched owner contacts are saved for this property. Contact enrichment is {status.replaceAll("_", " ")}.
        </div>
      )}

      {Object.keys(owner).length ? (
        <details className="bd-contact-raw">
          <summary>Complete owner profile</summary>
          <PropertyFields value={owner} />
        </details>
      ) : null}
    </section>
  );
}

function PropertyFields({ value }: { value: unknown }) {
  if (value === null || typeof value !== "object")
    return <span>{formatCell(value)}</span>;
  const entries = Array.isArray(value)
    ? value.map((item, index) => [`${index + 1}`, item] as const)
    : Object.entries(value);
  if (!entries.length) return <span>No saved data</span>;
  return (
    <dl className="bd-property-fields">
      {entries.map(([key, item]) => (
        <div key={key}>
          <dt>{key.replace(/([a-z])([A-Z])/g, "$1 $2").replace(/_/g, " ")}</dt>
          <dd>
            {item !== null && typeof item === "object" ? (
              <details open={key === "data" || key === "stages"}>
                <summary>View fields</summary>
                <PropertyFields value={item} />
              </details>
            ) : (
              formatCell(item)
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}
function formatCell(value: unknown) {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
function UsageBar({
  label,
  value,
  cap,
}: {
  label: string;
  value: number;
  cap: number;
}) {
  const percent = cap ? Math.min(100, (value / cap) * 100) : 0;
  return (
    <div>
      <div>
        <strong>{label}</strong>
        <span>
          {money(value)} / {money(cap)} · {percent.toFixed(0)}%
        </span>
      </div>
      <progress value={percent} max="100" />
    </div>
  );
}
function NumberField({
  title,
  value,
  onChange,
}: {
  title: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {title}
      <input
        type="number"
        min="0"
        step="0.01"
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}
function NullableNumberField({
  title,
  value,
  onChange,
}: {
  title: string;
  value: number | null;
  onChange: (value: number | null) => void;
}) {
  return (
    <label>
      {title}
      <input
        type="number"
        min="0"
        step="0.01"
        value={value ?? ""}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? null : Number(event.target.value),
          )
        }
        placeholder="Required before monitoring"
      />
    </label>
  );
}
