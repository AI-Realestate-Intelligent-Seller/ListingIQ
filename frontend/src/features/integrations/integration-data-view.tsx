"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { requestJson } from "@/lib/api/http-client";
import { PropertyTable } from "./batchdata-view";

const root = "/platform-admin/integrations/data";
type Mode = "live" | "sandbox";
type Provider = { key: string; name: string; count: number };
type Brokerage = { id: string; name: string; recipient_name: string };
type Summary = {
  providers: Provider[];
  combined: number;
  unassigned: number;
  categories: string[];
  lead_statuses: string[];
  brokerages: Brokerage[];
};
type Property = {
  id?: number;
  address: string;
  owner?: string;
  phones?: { number: string; dnc: boolean }[];
  listing_status?: string;
  listing_price?: number;
  beds?: number;
  baths?: number;
  estimated_value?: number;
  property_type?: string;
  area_sqft?: number;
  equity_percent?: number;
  source_record_id?: number;
  table_data?: Record<string, unknown>;
  categories: string[];
  lead_status: string;
  sources?: { provider: string; source_id: string }[];
  brokerage_id?: string;
  provider?: string;
  source_id?: string;
  data?: Property;
};
type Page<T> = { items: T[]; total: number };
type Rule = {
  brokerage_id: string;
  quantity: number;
  categories: string[];
  lead_statuses: string[];
};
type Allocation = {
  property_id: number;
  address: string;
  categories: string[];
  lead_status: string;
  brokerage_name: string;
};
type Plan = {
  preview_hash: string;
  allocated: number;
  remaining: number;
  excluded: number;
  allocations: Allocation[];
  summaries: {
    brokerage_name: string;
    requested: number;
    allocated: number;
    shortfall: number;
  }[];
};
type Result = {
  run_id: string;
  allocated: number;
  leads_created: number;
  mode: Mode;
  created_at?: string;
  reason?: string;
};

async function api<T>(path: string, payload?: unknown, signal?: AbortSignal) {
  const session = readAuthSession();
  if (!session) throw new Error("Sign in to continue");
  return requestJson<T>(root + path, {
    accessToken: session.access_token,
    method: payload === undefined ? "GET" : "POST",
    payload,
    signal,
  });
}
const label = (value: string) => value.replaceAll("_", " ");

export function IntegrationWorkspaceNav({
  active = "providers",
  mode,
}: {
  active?: "providers" | "data" | "distribution";
  mode?: Mode;
}) {
  return (
    <nav
      className="integration-workspace-nav"
      aria-label="Integration workspace"
    >
      <Link
        className={active === "providers" ? "active" : ""}
        href="/platform-admin/integrations"
      >
        Providers
      </Link>
      <Link
        className={active === "data" ? "active" : ""}
        href={`/operations/integrations/data${mode ? `?mode=${mode}` : ""}`}
      >
        Data
      </Link>
      <Link
        className={active === "distribution" ? "active" : ""}
        href={`/operations/integrations/distribution${mode ? `?mode=${mode}` : ""}`}
      >
        Distribution
      </Link>
    </nav>
  );
}

export function IntegrationDataView({
  distribution = false,
  initialMode = "sandbox",
}: {
  distribution?: boolean;
  initialMode?: Mode;
}) {
  const [mode, setMode] = useState<Mode>(initialMode);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [combinedOpen, setCombinedOpen] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void api<Summary>(`/summary?mode=${mode}`, undefined, controller.signal)
      .then(setSummary)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [mode, revision]);
  const combine = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<{
        source_records: number;
        unique_properties: number;
        duplicates_removed: number;
      }>("/combine", { mode });
      setNotice(
        `Combined ${result.source_records} source records into ${result.unique_properties} properties; ${result.duplicates_removed} duplicates removed.`,
      );
      setRevision((value) => value + 1);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Could not combine data",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="integration-data-workspace">
      <header>
        <span>OPERATIONS / INTEGRATIONS</span>
        <h1>{distribution ? "Distribution" : "Data"}</h1>
        <p>
          {distribution
            ? "Divide prepared properties among brokerages by category and lead status."
            : "Review saved provider data and combine it into one inventory for distribution."}
        </p>
      </header>
      <IntegrationWorkspaceNav
        active={distribution ? "distribution" : "data"}
        mode={mode}
      />
      <div className="pr-actions">
        <label>
          Data mode{" "}
          <select
            aria-label="Data mode"
            value={mode}
            disabled={busy}
            onChange={(event) => {
              setMode(event.target.value as Mode);
              setSummary(null);
              setError("");
              setNotice("");
            }}
          >
            <option value="sandbox">Sandbox / test data</option>
            <option value="live">Live data</option>
          </select>
        </label>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => setRevision((value) => value + 1)}
        >
          Refresh data
        </button>
      </div>
      {mode === "sandbox" && (
        <p className="platform-card">
          Sandbox data stays separate. Distribution records test allocations
          without adding mock leads to brokerage lead pools.
        </p>
      )}
      {error && (
        <p className="platform-error" role="alert">
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {!summary ? (
        <p>Loading saved data…</p>
      ) : distribution ? (
        <Distribution
          key={mode}
          mode={mode}
          summary={summary}
          revision={revision}
          refresh={() => setRevision((value) => value + 1)}
          setWorking={setBusy}
        />
      ) : (
        <>
          <div className="integration-data-providers">
            {summary.providers.map((provider) => (
              <ProviderTable
                key={`${mode}-${provider.key}`}
                provider={provider}
                mode={mode}
                revision={revision}
              />
            ))}
          </div>
          <section className="platform-card">
            <div className="integration-provider-header">
              <h2>Combined data</h2>
              <button
                className="integration-provider-toggle"
                aria-label={`${combinedOpen ? "Collapse" : "Expand"} combined data`}
                aria-expanded={combinedOpen}
                aria-controls="combined-data-table"
                onClick={() => setCombinedOpen((value) => !value)}
              >
                <svg
                  className={combinedOpen ? "open" : ""}
                  width="20"
                  height="20"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="m9 5 7 7-7 7" />
                </svg>
              </button>
            </div>
            <p>
              {summary.combined} saved properties · {summary.unassigned}{" "}
              awaiting distribution. Combining uses saved data and makes no
              provider requests.
            </p>
            <div className="pr-actions">
              <button
                className="button"
                disabled={
                  busy || !summary.providers.some((provider) => provider.count)
                }
                onClick={() => void combine()}
              >
                {busy ? "Combining…" : "Combine Provider Data"}
              </button>
              <Link
                className="button secondary"
                href={`/operations/integrations/distribution?mode=${mode}`}
              >
                Go to Distribution
              </Link>
            </div>
            {combinedOpen && (
              <div id="combined-data-table">
                <CombinedTable
                  key={mode}
                  mode={mode}
                  revision={revision}
                  brokerages={summary.brokerages}
                />
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function ProviderTable({
  provider,
  mode,
  revision,
}: {
  provider: Provider;
  mode: Mode;
  revision: number;
}) {
  const [page, setPage] = useState<Page<Property> | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void api<Page<Property>>(
      `/sources/${provider.key}?mode=${mode}&offset=${offset}`,
      undefined,
      controller.signal,
    )
      .then(setPage)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [provider.key, mode, offset, revision]);
  return (
    <section className="platform-card">
      <div className="integration-provider-header">
        <h2>{provider.name}</h2>
        <button
          className="integration-provider-toggle"
          aria-label={`${open ? "Collapse" : "Expand"} ${provider.name} data`}
          aria-expanded={open}
          aria-controls={`provider-data-${provider.key}`}
          onClick={() => setOpen((value) => !value)}
        >
          <svg
            className={open ? "open" : ""}
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            aria-hidden="true"
          >
            <path d="m9 5 7 7-7 7" />
          </svg>
        </button>
      </div>
      <p>{provider.count} saved properties</p>
      {open && (
        <div id={`provider-data-${provider.key}`}>
          {error && <p className="platform-error">{error}</p>}
          {page ? (
            <>
              <DataTable
                rows={page.items.map((row) => ({
                  ...row.data!,
                  categories: row.categories,
                  lead_status: row.lead_status,
                  source_id: row.source_id,
                  source_record_id: row.source_record_id,
                  table_data: row.table_data,
                }))}
                loadDetail={(id) =>
                  api(`/sources/${provider.key}/${id}?mode=${mode}`)
                }
              />
              <Pagination
                offset={offset}
                total={page.total}
                change={setOffset}
              />
            </>
          ) : (
            <p>Loading provider data…</p>
          )}
        </div>
      )}
    </section>
  );
}

function CombinedTable({
  mode,
  revision,
  unassigned = false,
}: {
  mode: Mode;
  revision: number;
  brokerages: Brokerage[];
  unassigned?: boolean;
}) {
  const [page, setPage] = useState<Page<Property> | null>(null);
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<Page<Property>>(
      `/combined?mode=${mode}&unassigned=${unassigned}&offset=${offset}`,
      undefined,
      controller.signal,
    )
      .then(setPage)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [mode, offset, revision, unassigned]);
  return (
    <>
      {error && <p className="platform-error">{error}</p>}
      {page ? (
        <>
          <DataTable
            rows={page.items}
            loadDetail={(id) => api(`/combined/${id}`)}
          />
          <Pagination offset={offset} total={page.total} change={setOffset} />
        </>
      ) : (
        <p>Loading combined data…</p>
      )}
    </>
  );
}

function DataTable({
  rows,
  loadDetail,
}: {
  rows: Property[];
  loadDetail: (id: number) => Promise<unknown>;
}) {
  if (!rows.length) return <p>No saved properties in this data mode.</p>;
  return (
    <PropertyTable
      rows={rows.map((row) => ({
        ...row,
        property_id: row.source_id || row.id,
        ...row.table_data,
        record_id: row.source_record_id || row.id,
      }))}
      loadDetail={loadDetail}
    />
  );
}

function Pagination({
  offset,
  total,
  change,
  size = 25,
}: {
  offset: number;
  total: number;
  change: (offset: number) => void;
  size?: number;
}) {
  return (
    <div className="pr-actions">
      <button
        className="button secondary"
        disabled={offset === 0}
        onClick={() => change(Math.max(0, offset - size))}
      >
        Previous
      </button>
      <span>
        {total
          ? `${offset + 1}–${Math.min(offset + size, total)} of ${total}`
          : "0 records"}
      </span>
      <button
        className="button secondary"
        disabled={offset + size >= total}
        onClick={() => change(offset + size)}
      >
        Next
      </button>
    </div>
  );
}

const newRule = (): Rule => ({
  brokerage_id: "",
  quantity: 25,
  categories: [],
  lead_statuses: [],
});
function Distribution({
  mode,
  summary,
  revision,
  refresh,
  setWorking,
}: {
  mode: Mode;
  summary: Summary;
  revision: number;
  refresh: () => void;
  setWorking: (busy: boolean) => void;
}) {
  const [rules, setRules] = useState<Rule[]>([newRule()]);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [history, setHistory] = useState<Page<Result> | null>(null);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [previewOffset, setPreviewOffset] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<Page<Result>>(
      `/distribution/history?mode=${mode}&offset=${historyOffset}`,
      undefined,
      controller.signal,
    )
      .then(setHistory)
      .catch((reason) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [mode, revision, historyOffset]);
  const updateRule = (index: number, update: Partial<Rule>) => {
    setRules((previous) =>
      previous.map((rule, i) => (i === index ? { ...rule, ...update } : rule)),
    );
    setPlan(null);
    setResult(null);
  };
  const run = async (execute: boolean) => {
    setBusy(true);
    setWorking(true);
    setError("");
    try {
      if (execute) {
        if (
          !window.confirm(
            `${mode === "sandbox" ? "Record test allocations" : "Distribute to brokerage lead pools"} for ${plan?.allocated} properties?`,
          )
        )
          return;
        const value = await api<Result>("/distribution/execute", {
          mode,
          rules,
          confirmed: true,
          preview_hash: plan?.preview_hash,
          reason,
        });
        setResult(value);
        setPlan(null);
        refresh();
      } else {
        setResult(null);
        setPreviewOffset(0);
        setPlan(await api<Plan>("/distribution/preview", { mode, rules }));
      }
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Could not process distribution",
      );
    } finally {
      setBusy(false);
      setWorking(false);
    }
  };
  return (
    <>
      <section className="platform-card">
        <h2>Prepared inventory</h2>
        <p>
          {summary.unassigned} properties awaiting distribution. Properties with
          incomplete addresses or already present in a brokerage lead pool are
          excluded from allocation.
        </p>
        <CombinedTable
          mode={mode}
          revision={revision}
          brokerages={summary.brokerages}
          unassigned
        />
      </section>
      <section className="platform-card">
        <h2>Brokerage divisions</h2>
        <p>
          Set the quantity, categories and lead statuses for each division.
          Properties must match both filters; empty filters include all values.
          Rules run from top to bottom; each property is allocated once. Leads
          are placed in the brokerage’s shared pool under its active head or
          broker.
        </p>
        {!summary.brokerages.length && (
          <p>No brokerages have an active head or broker available.</p>
        )}
        <fieldset disabled={busy} className="integration-division-rules">
          {rules.map((rule, index) => (
            <div className="integration-division-rule" key={index}>
              <label>
                Brokerage {index + 1}
                <select
                  aria-label={`Brokerage ${index + 1}`}
                  value={rule.brokerage_id}
                  onChange={(event) =>
                    updateRule(index, { brokerage_id: event.target.value })
                  }
                >
                  <option value="">Choose brokerage</option>
                  {summary.brokerages.map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name} · {org.recipient_name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Quantity
                <input
                  aria-label={`Quantity ${index + 1}`}
                  type="number"
                  min={1}
                  max={10000}
                  value={rule.quantity}
                  onChange={(event) =>
                    updateRule(index, { quantity: Number(event.target.value) })
                  }
                />
              </label>
              <details>
                <summary>
                  Categories:{" "}
                  {rule.categories.length
                    ? `${rule.categories.length} selected`
                    : "All"}
                </summary>
                <div className="integration-filter-options">
                  {summary.categories.map((category) => (
                    <label key={category}>
                      <input
                        type="checkbox"
                        checked={rule.categories.includes(category)}
                        onChange={(event) =>
                          updateRule(index, {
                            categories: event.target.checked
                              ? [...rule.categories, category]
                              : rule.categories.filter(
                                  (item) => item !== category,
                                ),
                          })
                        }
                      />
                      {label(category)}
                    </label>
                  ))}
                </div>
              </details>
              <details>
                <summary>
                  Lead statuses:{" "}
                  {rule.lead_statuses.length
                    ? rule.lead_statuses.map(label).join(", ")
                    : "All"}
                </summary>
                <div className="integration-filter-options">
                  {summary.lead_statuses.map((status) => (
                    <label key={status}>
                      <input
                        type="checkbox"
                        checked={rule.lead_statuses.includes(status)}
                        onChange={(event) =>
                          updateRule(index, {
                            lead_statuses: event.target.checked
                              ? [...rule.lead_statuses, status]
                              : rule.lead_statuses.filter(
                                  (item) => item !== status,
                                ),
                          })
                        }
                      />
                      {label(status)}
                    </label>
                  ))}
                </div>
              </details>
              <button
                className="button secondary"
                disabled={rules.length === 1}
                onClick={() => {
                  setRules((previous) =>
                    previous.filter((_, i) => i !== index),
                  );
                  setPlan(null);
                }}
              >
                Remove division
              </button>
            </div>
          ))}
          <div className="pr-actions">
            <button
              className="button secondary"
              disabled={rules.length >= 100}
              onClick={() => {
                setRules((previous) => [...previous, newRule()]);
                setPlan(null);
              }}
            >
              Add division
            </button>
            <button
              className="button"
              disabled={
                !summary.unassigned ||
                rules.some(
                  (rule) =>
                    !rule.brokerage_id ||
                    rule.quantity < 1 ||
                    rule.quantity > 10000,
                )
              }
              onClick={() => void run(false)}
            >
              Preview Distribution
            </button>
          </div>
        </fieldset>
        {error && (
          <p className="platform-error" role="alert">
            {error}
          </p>
        )}
        {plan && (
          <div className="integration-distribution-preview">
            <h3>Distribution preview</h3>
            <p>
              {plan.allocated} allocated · {plan.remaining} remaining ·{" "}
              {plan.excluded} excluded
            </p>
            <div className="platform-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Brokerage</th>
                    <th>Requested</th>
                    <th>Allocated</th>
                    <th>Shortfall</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.summaries.map((item, index) => (
                    <tr key={index}>
                      <td>{item.brokerage_name}</td>
                      <td>{item.requested}</td>
                      <td>{item.allocated}</td>
                      <td>{item.shortfall}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="platform-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Property</th>
                    <th>Categories</th>
                    <th>Lead status</th>
                    <th>Brokerage</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.allocations
                    .slice(previewOffset, previewOffset + 25)
                    .map((item) => (
                      <tr key={item.property_id}>
                        <td>{item.address}</td>
                        <td>{item.categories.map(label).join(", ")}</td>
                        <td>{label(item.lead_status)}</td>
                        <td>{item.brokerage_name}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
            <Pagination
              offset={previewOffset}
              total={plan.allocations.length}
              change={setPreviewOffset}
            />
            <label>
              Distribution reason
              <input
                aria-label="Distribution reason"
                value={reason}
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            <button
              className="button"
              disabled={busy || !plan.allocated || reason.trim().length < 3}
              onClick={() => void run(true)}
            >
              {mode === "sandbox"
                ? "Confirm Test Distribution"
                : "Confirm Distribution"}
            </button>
          </div>
        )}
        {result && (
          <p role="status">
            {result.mode === "sandbox"
              ? "Test distribution recorded"
              : "Distribution completed"}
            : {result.allocated} allocated, {result.leads_created} brokerage
            leads created.
          </p>
        )}
      </section>
      <section className="platform-card">
        <h2>Distribution history</h2>
        {history?.items.length ? (
          <>
            <div className="platform-table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Run</th>
                    <th>Allocated</th>
                    <th>Leads created</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {history.items.map((item) => (
                    <tr key={item.run_id}>
                      <td>
                        {item.created_at
                          ? new Date(item.created_at).toLocaleString()
                          : "—"}
                      </td>
                      <td>{item.run_id}</td>
                      <td>{item.allocated}</td>
                      <td>{item.leads_created}</td>
                      <td>{item.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              offset={historyOffset}
              total={history.total}
              change={setHistoryOffset}
            />
          </>
        ) : (
          <p>No distributions yet.</p>
        )}
      </section>
    </>
  );
}
