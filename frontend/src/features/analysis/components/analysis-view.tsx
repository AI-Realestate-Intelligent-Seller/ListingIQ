"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { RetrievalProgress } from "@/components/loading/retrieval-progress";
import { fetchCampaignAudienceAnalysis, listCampaignsWithProgress } from "@/features/campaigns/api/campaigns-api";
import type { Campaign, CampaignAudienceDistribution } from "@/features/campaigns/types/campaigns.types";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession } from "@/features/auth/lib/session-guard";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";
import { dashboardPath } from "@/features/dashboard/components/dashboard-routes";
import { listExperiments } from "@/features/experiments/api/experiments-api";
import { ExperimentWorkspace } from "@/features/experiments/components/experiment-workspace";
import type { Experiment } from "@/features/experiments/types/experiments.types";
import { ApiRequestError } from "@/lib/api/http-client";
import type { QueryProgress } from "@/lib/api/query-progress";

import { campaignMetrics, groupCampaignsByExperiment, type AnalysisMetrics, type TemplateVariant } from "../lib/campaign-analysis";

type AreaOutcome = { key: string; label: string; audience: number; outreached: number; replied: number; positive_replies: number };
type ComparisonItem = { title: string; message: string; campaigns: Campaign[]; timezone: string };
type OutcomeFilter = "all" | "positive" | "replied" | "no_reply";
const CAMPAIGNS_PER_PAGE = 3;
const CHART_COLORS = ["#1f5c4d", "#b8863b", "#4d73a4", "#955f82", "#6f8248", "#b75d4d"];
const OUTCOME_FILTER_OPTIONS: ReadonlyArray<readonly [OutcomeFilter, string]> = [
  ["all", "All results"],
  ["positive", "Has positive replies"],
  ["replied", "Has any replies"],
  ["no_reply", "Outreached, no replies"],
];

function percent(value: number | null): string {
  return value === null ? "—" : `${(value * 100).toFixed(1)}%`;
}

function dateValue(value: string | null): Date | null {
  if (!value) return null;
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  return Number.isNaN(stamp.getTime()) ? null : stamp;
}

function formatSentAt(value: string | null, timezone: string): string {
  const stamp = dateValue(value);
  if (!stamp) return "Not sent";
  return stamp.toLocaleString([], { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit", timeZone: timezone });
}

function exampleMessage(template: string): string {
  const values: Record<string, string> = {
    first_name: "Alex", owner_name: "Alex Morgan", address: "123 Main Street", area: "Chicago",
    reason: "your property may be a good fit for a quick conversation.",
    brokerage: "Example Realty", agent_name: "Jordan Lee",
  };
  return template
    .replace(/\{\{\s*([A-Za-z_]+)\s*\}\}/g, (_match, token: string) => values[token.toLowerCase()] ?? "")
    .replace(/[ \t]+/g, " ")
    .replace(/\s+([,.?!])/g, "$1")
    .trim();
}

function toggleValue<T>(values: T[], value: T): T[] {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value];
}

function normalizedAreaLabel(label: string): string {
  const compact = label.trim().replace(/\s+/g, " ");
  if (!compact || compact !== compact.toUpperCase()) return compact;
  return compact.toLowerCase().replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
}

function areaOutcomes(campaignIds: number[], audienceByCampaign: Map<number, CampaignAudienceDistribution>): AreaOutcome[] {
  const combined = new Map<string, AreaOutcome>();
  for (const campaignId of campaignIds) {
    for (const area of audienceByCampaign.get(campaignId)?.area_outcomes ?? []) {
      const label = normalizedAreaLabel(area.label);
      const key = label.toLocaleLowerCase();
      const current = combined.get(key) ?? { key, label, audience: 0, outreached: 0, replied: 0, positive_replies: 0 };
      current.audience += area.audience;
      current.outreached += area.outreached;
      current.replied += area.replied;
      current.positive_replies += area.positive_replies;
      combined.set(key, current);
    }
  }
  const sorted = [...combined.values()].sort((a, b) => b.outreached - a.outreached || a.label.localeCompare(b.label));
  if (sorted.length <= 6) return sorted;
  const other = sorted.slice(5).reduce<AreaOutcome>((total, area) => ({
    ...total,
    audience: total.audience + area.audience,
    outreached: total.outreached + area.outreached,
    replied: total.replied + area.replied,
    positive_replies: total.positive_replies + area.positive_replies,
  }), { key: "__other__", label: "Other areas", audience: 0, outreached: 0, replied: 0, positive_replies: 0 });
  return [...sorted.slice(0, 5), other];
}

function MetricStrip({ metrics }: { metrics: AnalysisMetrics }) {
  return <div className="analysis-metric-strip">
    <div><span>Outreached</span><strong>{metrics.delivered.toLocaleString()}</strong></div>
    <div><span>Replied</span><strong>{metrics.replied.toLocaleString()}</strong></div>
    <div><span>Not replied</span><strong>{Math.max(metrics.delivered - metrics.replied, 0).toLocaleString()}</strong></div>
    <div><span>Positive</span><strong>{metrics.positiveReplies.toLocaleString()}</strong></div>
    <div><span>Reply rate</span><strong>{percent(metrics.replyRate)}</strong></div>
  </div>;
}

function AreaGroupedBarChart({ areas }: { areas: AreaOutcome[] }) {
  const series = [
    { key: "outreached", label: "Outreached", color: "#1f5c4d" },
    { key: "replied", label: "Replied", color: "#b8863b" },
    { key: "positive_replies", label: "Positive", color: "#955f82" },
  ] as const;
  const maximum = Math.max(1, ...areas.map((area) => area.outreached));
  const axisMaximum = Math.ceil(maximum / 10) * 10 || 1;
  const ticks = [axisMaximum, Math.round(axisMaximum * .75), Math.round(axisMaximum * .5), Math.round(axisMaximum * .25), 0];
  return <figure className="analysis-area-chart" aria-label="Grouped area results">
    <figcaption>{series.map((item) => <span key={item.key}><i style={{ background: item.color }} /><b>{item.label}</b></span>)}</figcaption>
    <div className="analysis-area-chart-body">
      <div className="analysis-area-chart-axis" aria-hidden="true">{ticks.map((tick, index) => <span key={`${tick}-${index}`}>{tick.toLocaleString()}</span>)}</div>
      <div className="analysis-area-chart-scroll">
        <div className="analysis-area-chart-plot" style={{ gridTemplateColumns: `repeat(${areas.length}, minmax(112px, 1fr))` }}>
          {areas.map((area) => <div className="analysis-area-chart-cluster" key={area.key}>
            <div className="analysis-area-chart-bars">{series.map((item) => {
              const value = area[item.key];
              return <div className="analysis-area-chart-bar" key={item.key} aria-label={`${area.label}, ${item.label}: ${value.toLocaleString()}`}>
                <span>{value.toLocaleString()}</span><i style={{ height: `${(value / axisMaximum) * 100}%`, background: item.color }} />
              </div>;
            })}</div>
            <strong>{area.label}</strong>
          </div>)}
        </div>
      </div>
    </div>
  </figure>;
}

function comparisonValues(campaigns: Campaign[]) {
  const metrics = campaignMetrics(campaigns.filter((campaign) => campaign.status !== "draft"));
  return {
    metrics,
    values: [metrics.delivered, metrics.replied, Math.max(metrics.delivered - metrics.replied, 0), metrics.positiveReplies],
  };
}

function ClusteredBarChart({ items }: { items: ComparisonItem[] }) {
  const categories = ["Outreached", "Replied", "Not replied", "Positive"];
  const series = items.map((item, index) => ({ ...item, ...comparisonValues(item.campaigns), color: CHART_COLORS[index % CHART_COLORS.length] }));
  const maximum = Math.max(1, ...series.flatMap((item) => item.values));
  const axisMaximum = Math.ceil(maximum / 10) * 10 || 1;
  const ticks = [axisMaximum, Math.round(axisMaximum * .75), Math.round(axisMaximum * .5), Math.round(axisMaximum * .25), 0];
  return <figure className="analysis-clustered-chart" aria-label="Grouped outcome comparison">
    <figcaption>
      {series.map((item) => <span key={item.title}><i style={{ background: item.color }} /><b>{item.title}</b><small>{percent(item.metrics.replyRate)} reply rate</small></span>)}
    </figcaption>
    <div className="analysis-chart-body">
      <div className="analysis-chart-axis" aria-hidden="true">{ticks.map((tick, index) => <span key={`${tick}-${index}`}>{tick.toLocaleString()}</span>)}</div>
      <div className="analysis-chart-plot">
        {categories.map((category, categoryIndex) => <div className="analysis-chart-cluster" key={category}>
          <div className="analysis-chart-bars">
            {series.map((item) => {
              const value = item.values[categoryIndex];
              return <div className="analysis-chart-bar-wrap" key={item.title} aria-label={`${item.title}, ${category}: ${value.toLocaleString()}`}>
                <span>{value.toLocaleString()}</span>
                <i style={{ height: `${(value / axisMaximum) * 100}%`, background: item.color }} />
              </div>;
            })}
          </div>
          <strong>{category}</strong>
        </div>)}
      </div>
    </div>
  </figure>;
}

function ComparisonCard({ title, message, campaigns, timezone }: ComparisonItem) {
  return <article className="analysis-comparison-card">
    <div><span>{title}</span><small>{campaigns.length} campaign {campaigns.length === 1 ? "run" : "runs"}</small></div>
    <pre>{message}</pre>
    <p><b>Time zone:</b> {timezone}</p>
  </article>;
}

export function AnalysisView() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const session = useMemo(() => readAuthSession(), []);
  const token = session?.access_token ?? "";
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [audiences, setAudiences] = useState<CampaignAudienceDistribution[]>([]);
  const [activeTemplateKey, setActiveTemplateKey] = useState("");
  const [templateSelection, setTemplateSelection] = useState<string[]>([]);
  const [campaignSelection, setCampaignSelection] = useState<number[]>([]);
  const [campaignPage, setCampaignPage] = useState(1);
  const [compareMode, setCompareMode] = useState<"templates" | "campaigns">("templates");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState<OutcomeFilter>("all");
  const [isOutcomeFilterOpen, setIsOutcomeFilterOpen] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState("");
  const [loadProgress, setLoadProgress] = useState<QueryProgress<Campaign[]> | null>(null);
  const [detailPanelHeight, setDetailPanelHeight] = useState<number | null>(null);
  const outcomeFilterRef = useRef<HTMLDivElement | null>(null);
  const detailPanelRef = useRef<HTMLElement | null>(null);
  const analysisBase = pathname.match(/^(.*\/analysis)(?:\/|$)/)?.[1] ?? pathname;
  const analysisTail = pathname.slice(analysisBase.length).replace(/^\//, "").split("/").filter(Boolean);
  const routeSection = analysisTail[0];
  const routeId = analysisTail[1] ? decodeURIComponent(analysisTail[1]) : undefined;
  const comparisonType = routeSection === "comparison" && (analysisTail[1] === "campaigns" || analysisTail[1] === "experiments") ? analysisTail[1] : undefined;
  const comparisonIds = useMemo(() => (searchParams.get("ids") ?? "").split(",").map(Number).filter((id) => Number.isSafeInteger(id) && id > 0), [searchParams]);

  const openManagementRoute = useCallback((section: "drafted" | "launched", id?: string | number) => {
    const suffix = id === undefined ? "" : `/${encodeURIComponent(String(id))}`;
    router.push(`${analysisBase}/${section}${suffix}`, { scroll: false });
  }, [analysisBase, router]);

  const load = useCallback(async () => {
    if (!token) return;
    setIsLoading(true);
    try {
      const [campaignRows, audienceRows, experimentRows] = await Promise.all([
        listCampaignsWithProgress(token, setLoadProgress), fetchCampaignAudienceAnalysis(token), listExperiments(token),
      ]);
      setCampaigns(campaignRows);
      setAudiences(audienceRows);
      setExperiments(experimentRows);
      setErrorMessage("");
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 401) {
        endSession();
        router.replace("/login?expired=1");
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : "Could not load campaign analysis.");
    } finally {
      setIsLoading(false);
    }
  }, [router, token]);

  useEffect(() => {
    // Mounting this workspace starts the same remote campaign query used by Campaigns.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  useEffect(() => {
    const detail = detailPanelRef.current;
    if (!detail) {
      setDetailPanelHeight(null);
      return;
    }
    const updateHeight = () => {
      const height = Math.ceil(detail.getBoundingClientRect().height);
      if (height > 0) setDetailPanelHeight(height);
    };
    updateHeight();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateHeight);
    observer?.observe(detail);
    window.addEventListener("resize", updateHeight);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateHeight);
    };
  }, [activeTemplateKey]);

  // Analysis starts only after real outreach. Drafts and zero-delivery runs stay in Campaigns.
  const analyzedCampaigns = useMemo(
    () => campaigns.filter((campaign) => campaign.delivered > 0),
    [campaigns],
  );
  const filteredCampaigns = useMemo(() => analyzedCampaigns.filter((campaign) => {
    const sentAt = dateValue(campaign.sent_at);
    if (dateFrom && (!sentAt || sentAt < new Date(`${dateFrom}T00:00:00`))) return false;
    if (dateTo && (!sentAt || sentAt > new Date(`${dateTo}T23:59:59.999`))) return false;
    if (outcomeFilter === "positive" && campaign.positive_replies === 0) return false;
    if (outcomeFilter === "replied" && campaign.replied === 0) return false;
    if (outcomeFilter === "no_reply" && campaign.replied > 0) return false;
    return true;
  }), [analyzedCampaigns, dateFrom, dateTo, outcomeFilter]);
  const filtersActive = Boolean(dateFrom || dateTo || outcomeFilter !== "all");
  const allVariants = useMemo(() => groupCampaignsByExperiment(analyzedCampaigns, experiments), [analyzedCampaigns, experiments]);
  const experimentNumberByKey = useMemo(() => {
    const result = new Map<string, number>();
    let legacyNumber = Math.max(0, ...allVariants.map((variant) => variant.experimentId ?? 0)) + 1;
    [...allVariants].sort((a, b) => a.latestAt - b.latestAt).forEach((variant) => {
      result.set(variant.key, variant.experimentId ?? legacyNumber++);
    });
    return result;
  }, [allVariants]);
  const variants = useMemo(() => groupCampaignsByExperiment(filteredCampaigns, experiments), [filteredCampaigns, experiments]);
  const audienceByCampaign = useMemo(() => new Map(audiences.map((audience) => [audience.campaign_id, audience])), [audiences]);
  const localTimezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);
  const timezoneByCampaign = useMemo(() => {
    const result = new Map<number, string>();
    for (const experiment of experiments) {
      for (const campaignId of experiment.campaign_ids) result.set(campaignId, experiment.timezone);
    }
    return result;
  }, [experiments]);
  const routedExperimentId = routeSection === "experiments" ? Number(routeId) : 0;
  const routedVariant = Number.isSafeInteger(routedExperimentId) && routedExperimentId > 0
    ? variants.find((variant) => experimentNumberByKey.get(variant.key) === routedExperimentId)
    : undefined;
  const activeVariant = routedVariant ?? variants.find((variant) => variant.key === activeTemplateKey) ?? variants[0];
  const activeAreas = activeVariant ? areaOutcomes(activeVariant.campaigns.map((campaign) => campaign.id), audienceByCampaign) : [];
  const campaignPages = Math.max(1, Math.ceil((activeVariant?.campaigns.length ?? 0) / CAMPAIGNS_PER_PAGE));
  const currentCampaignPage = Math.min(campaignPage, campaignPages);
  const visibleCampaigns = activeVariant?.campaigns.slice(
    (currentCampaignPage - 1) * CAMPAIGNS_PER_PAGE,
    currentCampaignPage * CAMPAIGNS_PER_PAGE,
  ) ?? [];
  const testedCampaigns = filteredCampaigns;
  const overall = campaignMetrics(testedCampaigns);
  const comparedTemplates = templateSelection.map((key) => variants.find((variant) => variant.key === key)).filter((variant): variant is TemplateVariant => Boolean(variant));
  const comparedCampaigns = campaignSelection.map((id) => filteredCampaigns.find((campaign) => campaign.id === id)).filter((campaign): campaign is Campaign => Boolean(campaign));
  const templateComparisonItems = comparedTemplates.map((variant) => ({
    title: variant.name, message: variant.messages.map((item) => `${item.label}: ${item.template}`).join("\n\n"), campaigns: variant.campaigns,
    timezone: variant.campaigns.map((campaign) => timezoneByCampaign.get(campaign.id)).find(Boolean) ?? localTimezone,
  }));
  const campaignComparisonItems = comparedCampaigns.map((campaign) => ({
    title: campaign.name, message: campaign.message_template, campaigns: [campaign],
    timezone: timezoneByCampaign.get(campaign.id) ?? localTimezone,
  }));

  useEffect(() => {
    if (!comparisonType) return;
    if (comparisonType === "experiments") {
      const keys = variants
        .filter((variant) => comparisonIds.includes(experimentNumberByKey.get(variant.key) ?? 0))
        .map((variant) => variant.key);
      // The comparison URL is the source of truth after refresh/back/forward.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setCompareMode("templates");
      setTemplateSelection(keys);
    } else {
      setCompareMode("campaigns");
      setCampaignSelection(comparisonIds.filter((id) => filteredCampaigns.some((campaign) => campaign.id === id)));
    }
  }, [comparisonIds, comparisonType, experimentNumberByKey, filteredCampaigns, variants]);

  useEffect(() => {
    if (!isOutcomeFilterOpen) return;
    const close = (event: PointerEvent) => {
      if (!outcomeFilterRef.current?.contains(event.target as Node)) setIsOutcomeFilterOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOutcomeFilterOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isOutcomeFilterOpen]);

  function recordComparison(mode: "templates" | "campaigns", templateKeys = templateSelection, campaignIds = campaignSelection) {
    const ids = mode === "templates"
      ? templateKeys.map((key) => experimentNumberByKey.get(key)).filter((id): id is number => Boolean(id))
      : campaignIds;
    const query = ids.length ? `?ids=${ids.join(",")}` : "";
    router.replace(`${analysisBase}/comparison/${mode === "templates" ? "experiments" : "campaigns"}${query}`, { scroll: false });
  }

  return <section className="analysis-view">
    <header className="analysis-header">
      <div><span className="sms-eyebrow">A/B/N TESTING</span><h1 className="view-title">Analysis</h1><p>See which message was sent, who received it, and what happened next.</p></div>
      <div className="analysis-header-actions"><button type="button" className="button" onClick={() => { setShowCreate(true); openManagementRoute("drafted", "new"); }}>+ New experiment</button><NotificationBell /></div>
    </header>

    <section className="analysis-general-filters" aria-label="Filter experiments">
      <div className="analysis-filter-intro"><span>FILTER EXPERIMENTS</span><strong>Apply to experiment results, campaign runs, comparisons, and scheduled experiments</strong></div>
      <label><span>From</span><input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
      <label><span>To</span><input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
      <div className="analysis-result-filter" ref={outcomeFilterRef}><span id="analysis-result-filter-label">Reply result</span><button type="button" className="analysis-result-filter-trigger" aria-labelledby="analysis-result-filter-label analysis-result-filter-value" aria-haspopup="listbox" aria-expanded={isOutcomeFilterOpen} onClick={() => setIsOutcomeFilterOpen((open) => !open)}><span id="analysis-result-filter-value">{OUTCOME_FILTER_OPTIONS.find(([value]) => value === outcomeFilter)?.[1] ?? "All results"}</span><i aria-hidden="true" /></button>{isOutcomeFilterOpen ? <div className="analysis-result-filter-menu" role="listbox" aria-labelledby="analysis-result-filter-label">{OUTCOME_FILTER_OPTIONS.map(([value, label]) => <button type="button" role="option" aria-selected={outcomeFilter === value} className={outcomeFilter === value ? "selected" : ""} key={value} onClick={() => { setOutcomeFilter(value); setIsOutcomeFilterOpen(false); }}><span>{label}</span>{outcomeFilter === value ? <b aria-hidden="true">✓</b> : null}</button>)}</div> : null}</div>
      {filtersActive ? <button type="button" onClick={() => { setDateFrom(""); setDateTo(""); setOutcomeFilter("all"); setIsOutcomeFilterOpen(false); }}>Clear filters</button> : null}
    </section>

    <ExperimentWorkspace token={token} campaigns={campaigns} dateFrom={dateFrom} dateTo={dateTo} outcomeFilter={outcomeFilter} routeSection={routeSection === "drafted" || routeSection === "launched" ? routeSection : undefined} routeId={routeId} onRouteChange={openManagementRoute} open={showCreate} onClose={() => setShowCreate(false)} onUnauthorized={() => { endSession(); router.replace("/login?expired=1"); }} />

    {errorMessage ? <div className="analysis-error" role="alert"><span>{errorMessage}</span><button type="button" onClick={() => void load()}>Retry</button></div> : null}
    {isLoading ? <RetrievalProgress eyebrow="ANALYSIS" title="Building your campaign story" description="Grouping matching messages and calculating their outcomes." status="Retrieving campaign results" detail="Loading templates, campaigns, areas, and replies" ariaLabel="Loading campaign analysis" progress={loadProgress} progressUnit="campaigns" /> : null}

    {!isLoading && analyzedCampaigns.length > 0 ? <div className="analysis-scroll-area">
      <section className="analysis-summary" aria-label="Analysis totals">
        <article><span>Experiments</span><strong>{variants.length}</strong><small>Variant campaigns stay grouped under their experiment</small></article>
        <article><span>Total campaigns</span><strong>{filteredCampaigns.length}</strong><small>Only campaigns matching the current filters are included</small></article>
        <article><span>Leads outreached</span><strong>{overall.delivered.toLocaleString()}</strong><small>{overall.replied.toLocaleString()} replied</small></article>
        <article><span>Positive responses</span><strong>{overall.positiveReplies.toLocaleString()}</strong><small>{percent(overall.replyRate)} overall reply rate</small></article>
      </section>

      {filteredCampaigns.length ? <><section className="analysis-compare-toolbar">
        <div><span>COMPARE</span><strong>Select two or more experiments or campaign runs</strong></div>
        <div role="group" aria-label="Comparison type">
          <button type="button" className={compareMode === "templates" ? "active" : undefined} onClick={() => { setCompareMode("templates"); recordComparison("templates"); }}>Experiments <b>{comparedTemplates.length}</b></button>
          <button type="button" className={compareMode === "campaigns" ? "active" : undefined} onClick={() => { setCompareMode("campaigns"); recordComparison("campaigns"); }}>Campaigns <b>{comparedCampaigns.length}</b></button>
        </div>
      </section>

      <div className="analysis-browser">
        <aside className="analysis-template-list" aria-label="Experiments" style={detailPanelHeight ? { maxHeight: detailPanelHeight } : undefined}>
          <div className="analysis-template-list-header"><span>EXPERIMENTS</span><small>{variants.length} unique</small></div>
          <div className="analysis-template-list-scroll">
            {variants.map((variant) => <button key={variant.key} type="button" className={variant.key === activeVariant?.key ? "active" : undefined} onClick={() => { setActiveTemplateKey(variant.key); setCampaignPage(1); const experimentId = experimentNumberByKey.get(variant.key); if (experimentId) router.push(`${analysisBase}/experiments/${experimentId}`, { scroll: false }); }}>
              <span className="analysis-template-list-title"><strong>{variant.name}</strong><small>Experiment #{experimentNumberByKey.get(variant.key)}</small></span>
              <span className="analysis-template-list-meta"><small>{variant.messages.length} message {variant.messages.length === 1 ? "variant" : "variants"}</small><small>{variant.campaigns.length} campaign {variant.campaigns.length === 1 ? "run" : "runs"}</small></span>
              <span className="analysis-template-list-results"><em>{variant.metrics.delivered.toLocaleString()} outreached</em><em>{variant.metrics.replied.toLocaleString()} replied</em></span>
            </button>)}
          </div>
        </aside>

        {activeVariant ? <article className="analysis-template-detail" ref={detailPanelRef}>
          <header>
            <div><span>EXPERIMENT DETAIL</span><h2>{activeVariant.name}</h2><p>Experiment #{experimentNumberByKey.get(activeVariant.key)} · {activeVariant.campaigns.length} campaign runs · {activeVariant.messages.length} message variants</p></div>
            <div className="analysis-detail-actions"><a className="analysis-conversation-link" href={dashboardPath(session?.user.role ?? "broker", { view: "followups", campaign_ids: activeVariant.campaigns.map((campaign) => campaign.id).sort((a, b) => a - b) })}>View all conversations</a><label className="analysis-compare-check"><input type="checkbox" checked={templateSelection.includes(activeVariant.key)} onChange={() => { const next = toggleValue(templateSelection, activeVariant.key); setTemplateSelection(next); setCompareMode("templates"); recordComparison("templates", next); }} /> Select experiment to compare</label></div>
          </header>

          <section className="analysis-message-story">
            {activeVariant.messages.map((message) => <div key={`${message.campaignId}-${message.label}`}><span>{message.label.toUpperCase()}</span><pre>{message.template}</pre><small>Example</small><pre>{exampleMessage(message.template)}</pre></div>)}
          </section>

          <MetricStrip metrics={activeVariant.metrics} />

          <section className="analysis-area-section">
            <div><span>AREA RESULTS</span><h3>Outreach and responses by area</h3></div>
            {activeAreas.length ? <AreaGroupedBarChart areas={activeAreas} /> : <p className="analysis-empty-note">No area outcome data is available yet.</p>}
          </section>

          <section className="analysis-runs">
            <div><span>CAMPAIGN RUNS</span><h3>Variants and campaign runs</h3></div>
            <div className="analysis-run-list">{visibleCampaigns.map((campaign) => {
              const metrics = campaignMetrics(campaign.status === "draft" ? [] : [campaign]);
              return <article key={campaign.id}>
                <label><input type="checkbox" checked={campaignSelection.includes(campaign.id)} onChange={() => { const next = toggleValue(campaignSelection, campaign.id); setCampaignSelection(next); setCompareMode("campaigns"); recordComparison("campaigns", templateSelection, next); }} /><span><strong>{campaign.name}</strong><small>{campaign.broker_name ?? "Campaign owner"}</small></span></label>
                <div><span>{formatSentAt(campaign.sent_at, timezoneByCampaign.get(campaign.id) ?? localTimezone)}</span><small>{timezoneByCampaign.get(campaign.id) ?? localTimezone}</small></div>
                <dl><div><dt>Outreached</dt><dd>{metrics.delivered}</dd></div><div><dt>Replied</dt><dd>{metrics.replied}</dd></div><div><dt>Positive</dt><dd>{metrics.positiveReplies}</dd></div></dl>
                <a className="analysis-conversation-link" href={dashboardPath(session?.user.role ?? "broker", { view: "followups", campaign_id: campaign.id })}>View conversations</a>
              </article>;
            })}</div>
            {campaignPages > 1 ? <nav className="analysis-run-pagination" aria-label="Campaign run pages">
              <button type="button" disabled={currentCampaignPage === 1} onClick={() => setCampaignPage(currentCampaignPage - 1)}>Previous</button>
              <span>Page {currentCampaignPage} of {campaignPages}</span>
              <button type="button" disabled={currentCampaignPage === campaignPages} onClick={() => setCampaignPage(currentCampaignPage + 1)}>Next</button>
            </nav> : null}
          </section>
        </article> : null}
      </div>

      {compareMode === "templates" && comparedTemplates.length >= 2 ? <section className="analysis-comparison">
        <header><div><span>EXPERIMENT COMPARISON</span></div><button type="button" onClick={() => { setTemplateSelection([]); recordComparison("templates", []); }}>Clear selection</button></header>
        <ClusteredBarChart items={templateComparisonItems} />
        <div className="analysis-comparison-messages">{templateComparisonItems.map((item) => <ComparisonCard key={item.title} {...item} />)}</div>
      </section> : null}
      {compareMode === "campaigns" && comparedCampaigns.length >= 2 ? <section className="analysis-comparison">
        <header><div><span>CAMPAIGN COMPARISON</span><h2>Selected campaign runs side by side</h2></div><button type="button" onClick={() => { setCampaignSelection([]); recordComparison("campaigns", templateSelection, []); }}>Clear selection</button></header>
        <ClusteredBarChart items={campaignComparisonItems} />
        <div className="analysis-comparison-messages">{campaignComparisonItems.map((item) => <ComparisonCard key={item.title} {...item} />)}</div>
      </section> : null}
      {((compareMode === "templates" && comparedTemplates.length === 1) || (compareMode === "campaigns" && comparedCampaigns.length === 1)) ? <p className="analysis-selection-hint">Select one more {compareMode === "templates" ? "experiment" : "campaign"} to open the comparison.</p> : null}</> : <div className="analysis-filter-empty"><strong>No experiments match these filters</strong><p>Change the date range or reply result to see more experiments.</p></div>}
    </div> : null}

    {!isLoading && analyzedCampaigns.length === 0 && !errorMessage ? <div className="dashboard-empty"><div>⌁</div><h3>No outreach to analyze yet</h3><p>Experiments appear here after at least one underlying campaign has outreach.</p><button type="button" className="button" onClick={() => setShowCreate(true)}>+ New experiment</button></div> : null}
  </section>;
}
