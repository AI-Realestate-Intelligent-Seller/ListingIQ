"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchLeadPool } from "@/features/leads/api/leads-api";
import type { Lead, LeadPoolResponse } from "@/features/leads/types/leads.types";
import type { Campaign } from "@/features/campaigns/types/campaigns.types";

import { cancelExperiment, createExperiment, listExperiments } from "../api/experiments-api";
import type { Experiment, ExperimentCreateInput, ExperimentDimension } from "../types/experiments.types";

type VariantDraft = {
  id: string;
  label: string;
  message: string;
  leads: Lead[];
  followup: boolean;
  followupMessage: string;
  followupDelay: number;
};

type SavedExperimentDraft = {
  id: string;
  updatedAt: string;
  step: number;
  name: string;
  notes: string;
  dimension: ExperimentDimension[];
  dimensionOther: string;
  winner: ExperimentCreateInput["winner_metric"];
  variants: VariantDraft[];
  signal: string;
  leadCount: string;
  allocation: "balanced" | "random";
  schedule: string;
  timezone: string;
};

type Props = {
  token: string;
  open: boolean;
  campaigns?: Campaign[];
  dateFrom?: string;
  dateTo?: string;
  outcomeFilter?: "all" | "positive" | "replied" | "no_reply";
  routeSection?: "drafted" | "launched";
  routeId?: string;
  onRouteChange?: (section: "drafted" | "launched", id?: string | number) => void;
  onClose: () => void;
  onUnauthorized: () => void;
};
const COST_PER_MESSAGE = 0.0195;
const SAMPLE = { name: "Alex Morgan", first_name: "Alex", address: "123 Main Street, Austin, TX 78701", area: "Downtown", city: "Austin", state: "TX", zip_code: "78701", agent_name: "Jordan Lee", brokerage: "Example Realty" };
const VARIABLES = ["name", "first_name", "address", "area", "city", "state", "zip_code", "agent_name", "brokerage"];
const EMPTY_POOL: LeadPoolResponse = { leads: [], map: { mappable_count: 0, pending_count: 0, failed_count: 0 }, facets: { total: 0, signals: {}, stages: {} }, locations: { states: [], cities: [], zips: [] }, signal_catalog: [], stage_catalog: [] };
const DEFAULT_NAME = "Seller outreach message test";
const DEFAULT_NOTES = "Compare which message earns more owner replies while keeping the audience, allocation, and launch time consistent.";
const DEFAULT_MESSAGES = [
  "Hi {{first_name}}, this is {{agent_name}} with {{brokerage}}. I’m reaching out about {{address}}. Would you be open to a quick conversation?",
  "Hi {{first_name}}, I wanted to ask whether you’d consider discussing your property at {{address}}. Is now a good time to connect?",
];
const DEFAULT_FOLLOWUP = "Hi {{first_name}}, just following up about {{address}}. Would you be open to a brief conversation?";
const STATUS_FILTER_OPTIONS = [
  ["all", "All statuses"],
  ["scheduled", "Scheduled"],
  ["launching", "Launching"],
  ["running", "Running"],
  ["failed", "Failed"],
] as const;
const TIME_ZONE_GROUPS = [
  { label: "United States", options: [
    ["America/New_York", "Eastern Time"], ["America/Chicago", "Central Time"],
    ["America/Denver", "Mountain Time"], ["America/Phoenix", "Arizona Time"],
    ["America/Los_Angeles", "Pacific Time"], ["America/Anchorage", "Alaska Time"],
    ["Pacific/Honolulu", "Hawaii Time"],
  ] },
  { label: "UTC", options: [["UTC", "Coordinated Universal Time"]] },
  { label: "International", options: [
    ["America/Toronto", "Toronto"], ["America/Vancouver", "Vancouver"],
    ["America/Mexico_City", "Mexico City"], ["America/Sao_Paulo", "São Paulo"],
    ["Europe/London", "London"], ["Europe/Paris", "Paris"], ["Europe/Berlin", "Berlin"],
    ["Asia/Dubai", "Dubai"], ["Asia/Karachi", "Karachi"], ["Asia/Kolkata", "India"],
    ["Asia/Singapore", "Singapore"], ["Asia/Tokyo", "Tokyo"],
    ["Australia/Sydney", "Sydney"], ["Pacific/Auckland", "Auckland"],
  ] },
] as const;

function variantLabel(index: number): string { return `Variant ${String.fromCharCode(65 + index)}`; }
function newVariant(index: number): VariantDraft {
  return { id: `${Date.now()}-${index}-${Math.random()}`, label: variantLabel(index), message: DEFAULT_MESSAGES[index] ?? DEFAULT_MESSAGES[1], leads: [], followup: false, followupMessage: DEFAULT_FOLLOWUP, followupDelay: 24 };
}
function draftStorageKey(token: string): string {
  try {
    const payload = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))) as { sub?: string; user_id?: string };
    return `listingiq:experiment-drafts:${payload.sub ?? payload.user_id ?? "current"}`;
  } catch { return "listingiq:experiment-drafts:current"; }
}
function readDrafts(token: string): SavedExperimentDraft[] {
  try {
    const value = JSON.parse(localStorage.getItem(draftStorageKey(token)) ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.map((draft) => ({
      ...draft,
      // Drafts saved before multi-select stored one string.
      dimension: Array.isArray(draft.dimension) ? draft.dimension : [draft.dimension || "tone"],
    }));
  } catch { return []; }
}
function writeDrafts(token: string, drafts: SavedExperimentDraft[]): void {
  localStorage.setItem(draftStorageKey(token), JSON.stringify(drafts));
}
function localDateInput(): string {
  const stamp = new Date(Date.now() + 60 * 60 * 1000);
  stamp.setMinutes(stamp.getMinutes() - stamp.getTimezoneOffset());
  return stamp.toISOString().slice(0, 16);
}
function scheduledLabel(value: string | null, timeZone: string): string {
  if (!value) return "Sent immediately";
  const stamp = new Date(/[Z+]$/.test(value) ? value : `${value}Z`);
  try { return stamp.toLocaleString([], { timeZone, dateStyle: "medium", timeStyle: "short" }); }
  catch { return stamp.toLocaleString(); }
}
function timeZoneOffset(timeZone: string): string {
  try {
    const part = new Intl.DateTimeFormat("en-US", { timeZone, timeZoneName: "shortOffset" }).formatToParts(new Date()).find((item) => item.type === "timeZoneName")?.value;
    return part ? ` · ${part.replace("GMT", "UTC")}` : "";
  } catch { return ""; }
}
function timeZoneName(timeZone: string): string {
  for (const group of TIME_ZONE_GROUPS) {
    for (const [value, label] of group.options) {
      if (value === timeZone) return label;
    }
  }
  return timeZone;
}
function zonedDateTimeToISOString(value: string, timeZone: string): string {
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const wallClockAsUtc = Date.UTC(year, month - 1, day, hour, minute);
  const formatter = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  });
  const offsetAt = (stamp: number) => {
    const parts = Object.fromEntries(formatter.formatToParts(new Date(stamp)).map((part) => [part.type, part.value]));
    return Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day), Number(parts.hour), Number(parts.minute), Number(parts.second)) - stamp;
  };
  let result = wallClockAsUtc - offsetAt(wallClockAsUtc);
  // Recheck at the resulting instant because the first guess may cross a DST boundary.
  result = wallClockAsUtc - offsetAt(result);
  return new Date(result).toISOString();
}
function preview(text: string): string {
  return text.replace(/\{\{\s*([A-Za-z_]+)\s*\}\}/g, (_all, key: string) => SAMPLE[key as keyof typeof SAMPLE] ?? `{{${key}}}`);
}
function words(text: string): number { return text.trim() ? text.trim().split(/\s+/).length : 0; }
function locationParts(lead: Lead): { area: string; zip: string } {
  const zip = lead.property_address?.match(/\b\d{5}(?:-\d{4})?\b/)?.[0]?.slice(0, 5) ?? "Unknown ZIP";
  return { area: lead.area?.trim() || "Unknown area", zip };
}
function distribute(leads: Lead[], count: number, mode: "balanced" | "random", groups: number): Lead[][] {
  const selected = [...leads].slice(0, count);
  if (mode === "random") selected.sort(() => Math.random() - .5);
  else selected.sort((a, b) => `${locationParts(a).area}|${locationParts(a).zip}`.localeCompare(`${locationParts(b).area}|${locationParts(b).zip}`));
  const result = Array.from({ length: groups }, () => [] as Lead[]);
  selected.forEach((lead, index) => result[index % groups].push(lead));
  return result;
}
function distribution(leads: Lead[], field: "area" | "zip"): [string, number][] {
  const counts = new Map<string, number>();
  leads.forEach((lead) => { const key = locationParts(lead)[field]; counts.set(key, (counts.get(key) ?? 0) + 1); });
  return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 4);
}
function compactLead(lead: Lead): Lead {
  // Drafts only need identity and distribution fields; phone/provider data stays server-side.
  return { id: lead.id, area: lead.area, property_address: lead.property_address } as Lead;
}
function dimensionLabel(dimensions: ExperimentDimension[], other: string): string {
  return dimensions.map((value) => value === "other" ? other || "other" : value.replace("_", " ")).join(", ");
}

function DifferencePreview({ variants }: { variants: VariantDraft[] }) {
  const base = new Set(variants[0]?.message.toLowerCase().split(/\s+/).filter(Boolean) ?? []);
  return <div className="experiment-differences">
    <div className="experiment-diff-legend"><span className="purple">Changed</span><span className="green">Added</span><span className="red">Only in control</span></div>
    {variants.map((variant, index) => <div key={variant.id}><strong>{variant.label}</strong><p>{variant.message.split(/(\s+)/).map((token, tokenIndex) => {
      if (/^\s+$/.test(token)) return token;
      const normalized = token.toLowerCase();
      const className = index === 0
        ? (variants.slice(1).some((item) => !item.message.toLowerCase().includes(normalized)) ? "red" : "")
        : (!base.has(normalized) ? "green" : (token !== variants[0].message.split(/\s+/)[tokenIndex] ? "purple" : ""));
      return <mark className={className} key={`${token}-${tokenIndex}`}>{token}</mark>;
    })}</p></div>)}
  </div>;
}

function ExperimentWizard({ token, initialDraft, onDone, onSave, onDiscard }: { token: string; initialDraft?: SavedExperimentDraft; onDone: (item: Experiment) => void; onSave: (draft: SavedExperimentDraft) => void; onDiscard: () => void }) {
  const [step, setStep] = useState(initialDraft?.step ?? 1);
  const [name, setName] = useState(initialDraft?.name ?? DEFAULT_NAME);
  const [notes, setNotes] = useState(initialDraft?.notes ?? DEFAULT_NOTES);
  const [dimension, setDimension] = useState<ExperimentDimension[]>(initialDraft?.dimension ?? ["tone"]);
  const [dimensionOther, setDimensionOther] = useState(initialDraft?.dimensionOther ?? "");
  const [winner, setWinner] = useState<ExperimentCreateInput["winner_metric"]>(initialDraft?.winner ?? "reply_rate");
  const [variants, setVariants] = useState<VariantDraft[]>(initialDraft?.variants ?? [newVariant(0), newVariant(1)]);
  const [activeVariant, setActiveVariant] = useState(0);
  const [pool, setPool] = useState(EMPTY_POOL);
  const [signal, setSignal] = useState(initialDraft?.signal ?? "");
  const [leadCount, setLeadCount] = useState(initialDraft?.leadCount ?? "100");
  const [allocation, setAllocation] = useState<"balanced" | "random">(initialDraft?.allocation ?? "balanced");
  const [schedule, setSchedule] = useState(initialDraft?.schedule ?? localDateInput);
  const [timezone, setTimezone] = useState(() => initialDraft?.timezone ?? (Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"));
  const [timeZoneOpen, setTimeZoneOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const updateVariant = (index: number, patch: Partial<VariantDraft>) => setVariants((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  const totalLeads = variants.reduce((sum, item) => sum + item.leads.length, 0);
  const totalMessages = totalLeads + variants.reduce((sum, item) => sum + (item.followup ? item.leads.length : 0), 0);

  function toggleTimeZoneMenu(): void {
    // Chromium can leave the native datetime popup open over a custom menu.
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
    setTimeZoneOpen((value) => !value);
  }

  function snapshot(): SavedExperimentDraft {
    return { id: initialDraft?.id ?? `${Date.now()}-${Math.random()}`, updatedAt: new Date().toISOString(), step, name, notes, dimension, dimensionOther, winner, variants: variants.map((variant) => ({ ...variant, leads: variant.leads.map(compactLead) })), signal, leadCount, allocation, schedule, timezone };
  }

  function validateStep(): boolean {
    let message = "";
    if (step === 1 && !name.trim()) message = "Give this experiment a name.";
    if (step === 1 && dimension.length === 0) message = "Select at least one thing to test.";
    if (step === 1 && dimension.includes("other") && !dimensionOther.trim()) message = "Describe what you are testing.";
    if (step === 2 && variants.some((item) => !item.message.trim())) message = "Write an opening message for every variant.";
    if (step === 3 && variants.some((item) => item.leads.length === 0)) message = "Create the audience groups before continuing.";
    if (step === 4 && !schedule) message = "Choose when the experiment should launch.";
    if (step === 4 && variants.some((item) => item.followup && !item.followupMessage.trim())) message = "Write the follow-up message for each enabled variant.";
    setError(message);
    return !message;
  }

  async function loadAudience(allowSmallerPool = false) {
    const count = Math.floor(Number(leadCount));
    if (!Number.isFinite(count) || count < variants.length) { setError(`Pick at least ${variants.length} leads so each group has an audience.`); return; }
    setBusy(true); setError("");
    try {
      const response = await fetchLeadPool({ signals: signal ? [signal] : undefined }, token);
      setPool(response);
      if (response.leads.length < variants.length) { setError(`Only ${response.leads.length} matching leads are currently available. At least ${variants.length} are needed.`); return; }
      if (response.leads.length < count && !allowSmallerPool) { setError(`Only ${response.leads.length} matching leads are currently available.`); return; }
      const selectedCount = Math.min(count, response.leads.length);
      if (selectedCount !== count) setLeadCount(String(selectedCount));
      const groups = distribute(response.leads, selectedCount, allocation, variants.length);
      setVariants((items) => items.map((item, index) => ({ ...item, leads: groups[index] })));
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not load the lead pool."); }
    finally { setBusy(false); }
  }

  async function launch(sendNow = false) {
    setBusy(true); setError("");
    try {
      const item = await createExperiment({ name: name.trim(), goal_notes: notes.trim(), test_dimension: dimension.join(","), test_dimension_other: dimensionOther.trim(), winner_metric: winner, allocation_mode: allocation, timezone, scheduled_at: sendNow ? null : zonedDateTimeToISOString(schedule, timezone), launch_now: sendNow, variants: variants.map((variant) => ({ label: variant.label, message_template: variant.message, lead_ids: variant.leads.map((lead) => lead.id), followup_enabled: variant.followup, followup_template: variant.followup ? variant.followupMessage : null, followup_delay_hours: variant.followup ? variant.followupDelay : null })) }, token);
      onDone(item);
    } catch (cause) { setError(cause instanceof Error ? cause.message : sendNow ? "Could not send the experiment." : "Could not schedule the experiment."); }
    finally { setBusy(false); }
  }

  const active = variants[activeVariant] ?? variants[0];
  return <div className="experiment-modal" role="dialog" aria-modal="true" aria-label={initialDraft ? "Edit experiment draft" : "New experiment"}>
    <div className="experiment-wizard">
      <header><div><span>{initialDraft ? "EXPERIMENT DRAFT" : "NEW A/B/N EXPERIMENT"}</span><h2>{name || "Untitled experiment"}</h2></div><button type="button" onClick={() => onSave(snapshot())} aria-label="Save and close experiment editor">×</button></header>
      <nav aria-label="Experiment steps">{["Setup", "Messages", "Audience", "Schedule", "Review"].map((label, index) => <button type="button" key={label} className={step === index + 1 ? "active" : step > index + 1 ? "done" : ""} onClick={() => index + 1 < step && setStep(index + 1)}><b>{index + 1}</b><span>{label}</span></button>)}</nav>
      <main>
        {step === 1 ? <section className="experiment-step"><div className="experiment-step-title"><span>STEP 1</span><h3>Define a clear test</h3><p>Name the experiment, record the goal, and decide what result makes a group the winner.</p></div><div className="experiment-form-grid">
          <label className="wide"><span>Experiment name</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="Spring seller message test" /></label>
          <label className="wide"><span>Goal / notes</span><textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="What do you want to learn or improve?" /></label>
          <fieldset className="wide"><legend>What are we testing?</legend><div className="experiment-choice-row experiment-pill-select">{[["tone", "Tone"], ["length", "Length"], ["reason", "Reason"], ["open_question", "Open questions"], ["other", "Other"]].map(([value, label]) => { const key = value as ExperimentDimension; return <label className={dimension.includes(key) ? "selected" : ""} key={value}><input type="checkbox" checked={dimension.includes(key)} onChange={() => setDimension((items) => items.includes(key) ? items.filter((item) => item !== key) : [...items, key])} />{label}</label>; })}</div>{dimension.includes("other") ? <input value={dimensionOther} onChange={(event) => setDimensionOther(event.target.value)} placeholder="Describe what else changes between groups" /> : null}</fieldset>
          <fieldset className="wide"><legend>How is the winning group decided?</legend><div className="experiment-choice-row"><label><input type="radio" checked={winner === "reply_rate"} onChange={() => setWinner("reply_rate")} />Reply rate</label><label><input type="radio" checked={winner === "positive_response_rate"} onChange={() => setWinner("positive_response_rate")} />Positive response rate</label></div></fieldset>
        </div></section> : null}

        {step === 2 ? <section className="experiment-step"><div className="experiment-step-title"><span>STEP 2</span><h3>Write the message variants</h3><p>Keep the audience constant and change only the element you want to test.</p></div><div className="experiment-variant-tabs">{variants.map((variant, index) => <button className={index === activeVariant ? "active" : ""} type="button" key={variant.id} onClick={() => setActiveVariant(index)}>{variant.label}</button>)}{variants.length < 8 ? <button type="button" onClick={() => setVariants((items) => [...items, newVariant(items.length)])}>+ Add group</button> : null}{variants.length > 2 ? <button type="button" className="remove" onClick={() => { setVariants((items) => items.filter((_item, index) => index !== activeVariant).map((item, index) => ({ ...item, label: variantLabel(index) }))); setActiveVariant(0); }}>Remove</button> : null}</div>
          <div className="experiment-message-editor"><label><span>{active.label} message</span><textarea value={active.message} onChange={(event) => updateVariant(activeVariant, { message: event.target.value })} placeholder="Hi {{first_name}}, I’m reaching out about {{address}}…" /></label><div className="experiment-editor-tools"><span>{active.message.length} characters · {words(active.message)} words</span><div>{VARIABLES.map((variable) => <button type="button" key={variable} onClick={() => updateVariant(activeVariant, { message: `${active.message}${active.message && !active.message.endsWith(" ") ? " " : ""}{{${variable}}}` })}>+ {variable.replace("_", " ")}</button>)}</div><div><button type="button" onClick={() => updateVariant(activeVariant, { message: active.message.toLowerCase() })}>lowercase</button><button type="button" onClick={() => updateVariant(activeVariant, { message: active.message.toUpperCase() })}>UPPERCASE</button></div></div><aside><span>EXAMPLE LEAD PREVIEW</span><p>{preview(active.message) || "Your personalized preview appears here."}</p></aside></div>
          {variants.every((item) => item.message.trim()) ? <DifferencePreview variants={variants} /> : null}
        </section> : null}

        {step === 3 ? <section className="experiment-step"><div className="experiment-step-title"><span>STEP 3</span><h3>Build comparable lead groups</h3><p>Leads are reserved now. Phone lookup and send-time processing happen only after launch.</p></div><div className="experiment-audience-controls"><label><span>Reason filter</span><select value={signal} onChange={(event) => setSignal(event.target.value)}><option value="">Any reason</option>{pool.signal_catalog.map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}</select></label><label><span>Number of leads</span><input type="number" min={variants.length} max="10000" value={leadCount} onChange={(event) => setLeadCount(event.target.value)} /></label></div><div className="experiment-allocation-row"><fieldset><legend>Group split</legend><div><label className={allocation === "balanced" ? "selected" : ""}><input type="radio" checked={allocation === "balanced"} onChange={() => setAllocation("balanced")} /><span><strong>Balanced</strong><small>Match area and ZIP across groups</small></span></label><label className={allocation === "random" ? "selected" : ""}><input type="radio" checked={allocation === "random"} onChange={() => setAllocation("random")} /><span><strong>Random</strong><small>Shuffle leads evenly by count</small></span></label></div></fieldset><button className="button" type="button" disabled={busy} onClick={() => void loadAudience()}>{busy ? "Creating groups…" : variants.some((item) => item.leads.length) ? "Recreate groups" : "Create groups"}</button></div>
          {totalLeads ? <section className="experiment-distribution"><header><div><span>GROUP DISTRIBUTION</span><h4>{allocation === "balanced" ? "Balanced by area and ZIP code" : "Randomly allocated"}</h4></div><small>{totalLeads.toLocaleString()} leads across {variants.length} groups</small></header><div className="experiment-distribution-table-wrap"><table className="experiment-distribution-table"><thead><tr><th scope="col" aria-label="Distribution field" />{variants.map((variant) => <th scope="col" key={`${variant.id}-heading`}><div><span>{variant.label}</span><small>{variant.leads.length} leads</small></div></th>)}</tr></thead><tbody><tr><th scope="row"><strong>Top areas</strong><small>Lead count</small></th>{variants.map((variant) => <td key={`${variant.id}-area`}><div className="distribution-cell">{distribution(variant.leads, "area").map(([label, count]) => <div key={label}><span>{label}</span><b>{count}</b></div>)}</div></td>)}</tr><tr><th scope="row"><strong>Top ZIP codes</strong><small>Lead count</small></th>{variants.map((variant) => <td key={`${variant.id}-zip`}><div className="distribution-cell">{distribution(variant.leads, "zip").map(([label, count]) => <div key={label}><span>{label}</span><b>{count}</b></div>)}</div></td>)}</tr></tbody></table></div></section> : <div className="experiment-placeholder">Choose filters and create groups to see the data distribution.</div>}
        </section> : null}

        {step === 4 ? <section className="experiment-step"><div className="experiment-step-title"><span>STEP 4</span><h3>Schedule launch and follow-ups</h3><p>The maximum estimate includes the initial message and every enabled follow-up.</p></div><div className="experiment-schedule"><label><span>Launch date and time</span><input type="datetime-local" value={schedule} onFocus={() => setTimeZoneOpen(false)} onChange={(event) => setSchedule(event.target.value)} /></label><div className="experiment-timezone-field"><span>Time zone</span><button type="button" className="experiment-timezone-trigger" aria-label="Time zone" aria-describedby="experiment-timezone-note" aria-haspopup="listbox" aria-expanded={timeZoneOpen} onClick={toggleTimeZoneMenu}><span>{timeZoneName(timezone)}{timeZoneOffset(timezone)}</span><i aria-hidden="true" /></button><small id="experiment-timezone-note">The launch time above is interpreted in this timezone.</small>{timeZoneOpen ? <div className="experiment-timezone-menu" role="listbox" aria-label="Choose time zone"><div className="experiment-timezone-current"><strong>Selected</strong><button type="button" role="option" aria-selected="true" className="selected" onClick={() => setTimeZoneOpen(false)}><span>{timeZoneName(timezone)}</span><small>{timeZoneOffset(timezone).replace(" · ", "")}</small><b>✓</b></button></div>{TIME_ZONE_GROUPS.map((group) => <section key={group.label}><strong>{group.label}</strong>{group.options.map(([value, label]) => <button type="button" role="option" aria-selected={timezone === value} className={timezone === value ? "selected" : ""} value={value} key={value} onClick={() => { setTimezone(value); setTimeZoneOpen(false); }}><span>{label}</span><small>{timeZoneOffset(value).replace(" · ", "")}</small>{timezone === value ? <b>✓</b> : null}</button>)}</section>)}</div> : null}</div><article><span>Expected maximum cost</span><strong>${(totalMessages * COST_PER_MESSAGE).toFixed(2)}</strong><small>{totalMessages.toLocaleString()} messages × $0.0195</small></article></div><div className="experiment-followups">{variants.map((variant, index) => <article key={variant.id}><label className="toggle"><input type="checkbox" checked={variant.followup} onChange={(event) => updateVariant(index, { followup: event.target.checked })} /><span><strong>{variant.label} follow-up</strong><small>Send only when the lead has not replied</small></span></label>{variant.followup ? <><label><span>Follow-up message</span><textarea value={variant.followupMessage} onChange={(event) => updateVariant(index, { followupMessage: event.target.value })} placeholder="Hi {{first_name}}, just following up…" /></label><div className="experiment-followup-meta"><span>{variant.followupMessage.length} characters · {words(variant.followupMessage)} words</span><label>Send after <input type="number" min="1" max="720" value={variant.followupDelay} onChange={(event) => updateVariant(index, { followupDelay: Number(event.target.value) })} /> hours</label></div></> : null}</article>)}</div>
        </section> : null}

        {step === 5 ? <section className="experiment-step"><div className="experiment-step-title"><span>STEP 5</span><h3>Review before launch</h3><p>Review everything, then schedule the experiment or send it immediately.</p></div><div className="experiment-review"><article><header><span>LAUNCH & COST</span><button type="button" onClick={() => setStep(4)}>Edit</button></header><h4>Schedule for {new Date(schedule).toLocaleString()} ({timezone})</h4><p>Or choose Send now below · Maximum ${(totalMessages * COST_PER_MESSAGE).toFixed(2)}</p></article><article><header><span>SETUP</span><button type="button" onClick={() => setStep(1)}>Edit</button></header><h4>{name}</h4><p>{notes || "No notes"}</p><small>Testing {dimensionLabel(dimension, dimensionOther)} · Winner by {winner.replaceAll("_", " ")}</small></article><article><header><span>AUDIENCE</span><button type="button" onClick={() => setStep(3)}>Edit</button></header><h4>{totalLeads} leads across {variants.length} groups</h4><p>{allocation === "balanced" ? "Balanced by area and ZIP code" : "Random allocation"}</p></article>{variants.map((variant, index) => <article className="message" key={variant.id}><header><span>{variant.label.toUpperCase()}</span><button type="button" onClick={() => { setActiveVariant(index); setStep(2); }}>Edit</button></header><pre>{variant.message}</pre><small>{variant.leads.length} leads{variant.followup ? ` · Follow-up after ${variant.followupDelay} hours` : " · No follow-up"}</small></article>)}</div></section> : null}
      </main>
      {error ? <p className="experiment-error" role="alert">{error}</p> : null}
      <footer><div className="experiment-draft-actions"><button type="button" className="sms-button-secondary" onClick={() => onSave(snapshot())}>Save draft</button><button type="button" className="experiment-discard" onClick={onDiscard}>Discard</button>{step > 1 ? <button type="button" className="sms-button-secondary" onClick={() => { setError(""); setStep((value) => value - 1); }}>Back</button> : null}</div><span>Step {step} of 5</span>{step < 5 ? <button type="button" className="button" disabled={busy} onClick={() => { if (validateStep()) { const nextStep = step + 1; setStep(nextStep); if (nextStep === 3 && totalLeads === 0) void loadAudience(true); } }}>{busy ? "Preparing…" : "Continue"}</button> : <div className="experiment-launch-actions"><button type="button" className="sms-button-secondary" disabled={busy} onClick={() => void launch()}>{busy ? "Working…" : "Schedule experiment"}</button><button type="button" className="button" disabled={busy} onClick={() => void launch(true)}>{busy ? "Sending…" : "Send now"}</button></div>}</footer>
    </div>
  </div>;
}

export function ExperimentWorkspace({ token, open, campaigns = [], dateFrom = "", dateTo = "", outcomeFilter = "all", routeSection, routeId, onRouteChange, onClose, onUnauthorized }: Props) {
  const [items, setItems] = useState<Experiment[]>([]);
  const [drafts, setDrafts] = useState<SavedExperimentDraft[]>([]);
  const [activeScreen, setActiveScreen] = useState<"drafts" | "scheduled">("scheduled");
  const [statusFilter, setStatusFilter] = useState("all");
  const [isStatusFilterOpen, setIsStatusFilterOpen] = useState(false);
  const [editingDraft, setEditingDraft] = useState<SavedExperimentDraft | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [closedDraftRoute, setClosedDraftRoute] = useState<string | null>(null);
  const statusFilterRef = useRef<HTMLDivElement | null>(null);
  const load = useCallback(async () => { if (!token) return; try { setItems(await listExperiments(token)); } catch (cause) { if ((cause as { status?: number }).status === 401) onUnauthorized(); else setError(cause instanceof Error ? cause.message : "Could not load experiments."); } }, [onUnauthorized, token]);
  useEffect(() => {
    // Loading is an external API synchronization, not derived local state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);
  useEffect(() => {
    // Browser-local drafts intentionally survive a refresh without reserving leads.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDrafts(readDrafts(token));
  }, [token]);
  useEffect(() => {
    if (!routeSection) return;
    // A copied Analysis URL restores the same management screen.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveScreen(routeSection === "launched" ? "scheduled" : "drafts");
    if (routeSection === "launched") {
      const id = Number(routeId);
      setExpanded(Number.isSafeInteger(id) && id > 0 ? id : null);
    }
  }, [routeId, routeSection]);
  useEffect(() => {
    if (closedDraftRoute && closedDraftRoute !== routeId) {
      // Moving away makes that draft URL eligible to open again later.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setClosedDraftRoute(null);
    }
    if (routeSection !== "drafted" || !routeId || routeId === "new") return;
    if (closedDraftRoute === routeId) return;
    const draft = drafts.find((item) => item.id === routeId);
    if (draft) {
      // Browser navigation can open a persisted local draft after it is loaded.
      setEditingDraft(draft);
    }
  }, [closedDraftRoute, drafts, routeId, routeSection]);
  useEffect(() => {
    if (!isStatusFilterOpen) return;
    const close = (event: PointerEvent) => {
      if (!statusFilterRef.current?.contains(event.target as Node)) setIsStatusFilterOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsStatusFilterOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isStatusFilterOpen]);
  const visible = useMemo(() => items.filter((item) => item.status !== "cancelled"), [items]);
  const campaignById = useMemo(() => new Map(campaigns.map((campaign) => [campaign.id, campaign])), [campaigns]);
  const experimentOutcomes = useMemo(() => new Map(visible.map((item) => {
    const rows = item.campaign_ids.map((id) => campaignById.get(id)).filter((campaign): campaign is Campaign => Boolean(campaign));
    return [item.id, {
      outreached: rows.reduce((sum, campaign) => sum + campaign.delivered, 0),
      replied: rows.reduce((sum, campaign) => sum + campaign.replied, 0),
      positive: rows.reduce((sum, campaign) => sum + campaign.positive_replies, 0),
    }];
  })), [campaignById, visible]);
  const filteredVisible = useMemo(() => visible.filter((item) => {
    const scheduled = new Date(/[Z+]$/.test(item.scheduled_at) ? item.scheduled_at : `${item.scheduled_at}Z`);
    if (dateFrom && scheduled < new Date(`${dateFrom}T00:00:00`)) return false;
    if (dateTo && scheduled > new Date(`${dateTo}T23:59:59.999`)) return false;
    if (statusFilter !== "all" && item.status !== statusFilter) return false;
    const outcome = experimentOutcomes.get(item.id) ?? { outreached: 0, replied: 0, positive: 0 };
    if (outcomeFilter === "positive" && outcome.positive === 0) return false;
    if (outcomeFilter === "replied" && outcome.replied === 0) return false;
    if (outcomeFilter === "no_reply" && (outcome.outreached === 0 || outcome.replied > 0)) return false;
    return true;
  }), [dateFrom, dateTo, experimentOutcomes, outcomeFilter, statusFilter, visible]);
  const filtersActive = Boolean(dateFrom || dateTo || statusFilter !== "all" || outcomeFilter !== "all");
  async function cancel(item: Experiment) { try { const updated = await cancelExperiment(item.id, token); setItems((rows) => rows.map((row) => row.id === updated.id ? updated : row)); } catch (cause) { setError(cause instanceof Error ? cause.message : "Could not cancel the experiment."); } }
  function saveDraft(draft: SavedExperimentDraft) {
    const next = [draft, ...drafts.filter((item) => item.id !== draft.id)].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    setClosedDraftRoute(routeId ?? "new");
    setDrafts(next); writeDrafts(token, next); setEditingDraft(null); setActiveScreen("drafts"); onRouteChange?.("drafted"); onClose();
  }
  function removeDraft(id: string | undefined, updateRoute = true) {
    const next = id ? drafts.filter((item) => item.id !== id) : drafts;
    setClosedDraftRoute(routeId ?? "new");
    setDrafts(next); writeDrafts(token, next); setEditingDraft(null); if (updateRoute) onRouteChange?.("drafted"); onClose();
  }
  const opensNewRoute = routeSection === "drafted" && routeId === "new" && closedDraftRoute !== "new";
  return <>
    {open || opensNewRoute || editingDraft ? <ExperimentWizard key={editingDraft?.id ?? "new"} token={token} initialDraft={editingDraft ?? undefined} onSave={saveDraft} onDiscard={() => removeDraft(editingDraft?.id)} onDone={(item) => { removeDraft(editingDraft?.id, false); setItems((rows) => [item, ...rows]); setActiveScreen("scheduled"); setExpanded(item.id); onRouteChange?.("launched", item.id); }} /> : null}
    <section className="experiment-list experiment-management">
      <header><div><span>EXPERIMENTS</span><h2>Launched and drafted experiments</h2></div><small>{drafts.length + visible.length} total</small></header>
      <nav className="experiment-screen-tabs" aria-label="Experiment status"><button type="button" className={activeScreen === "scheduled" ? "active" : ""} onClick={() => { setActiveScreen("scheduled"); onRouteChange?.("launched"); }}>Launched <b>{visible.length}</b></button><button type="button" className={activeScreen === "drafts" ? "active" : ""} onClick={() => { setActiveScreen("drafts"); onRouteChange?.("drafted"); }}>Drafted <b>{drafts.length}</b></button></nav>
      {activeScreen === "scheduled" ? <div className="experiment-status-filter"><div className="experiment-status-filter-field" ref={statusFilterRef}><span id="experiment-status-filter-label">Status</span><button type="button" className="experiment-status-filter-trigger" aria-labelledby="experiment-status-filter-label experiment-status-filter-value" aria-haspopup="listbox" aria-expanded={isStatusFilterOpen} onClick={() => setIsStatusFilterOpen((open) => !open)}><span id="experiment-status-filter-value">{STATUS_FILTER_OPTIONS.find(([value]) => value === statusFilter)?.[1] ?? "All statuses"}</span><i aria-hidden="true" /></button>{isStatusFilterOpen ? <div className="experiment-status-filter-menu" role="listbox" aria-labelledby="experiment-status-filter-label">{STATUS_FILTER_OPTIONS.map(([value, label]) => <button type="button" role="option" aria-selected={statusFilter === value} className={statusFilter === value ? "selected" : ""} key={value} onClick={() => { setStatusFilter(value); setIsStatusFilterOpen(false); }}><span>{label}</span>{statusFilter === value ? <b aria-hidden="true">✓</b> : null}</button>)}</div> : null}</div>{statusFilter !== "all" ? <button type="button" onClick={() => { setStatusFilter("all"); setIsStatusFilterOpen(false); }}>Clear status</button> : null}</div> : null}
      {error ? <p className="experiment-error">{error}</p> : null}
      {activeScreen === "drafts" ? <div className="experiment-screen-body">{drafts.length ? drafts.map((draft) => {
        const leadTotal = draft.variants.reduce((sum, variant) => sum + variant.leads.length, 0);
        return <article className="experiment-draft-card" key={draft.id}><header><div><span className="experiment-status draft">Draft</span><h3>{draft.name || "Untitled experiment"}</h3><p>Updated {new Date(draft.updatedAt).toLocaleString()}</p></div><div><button type="button" onClick={() => { setEditingDraft(draft); onRouteChange?.("drafted", draft.id); }}>Continue editing</button><button type="button" className="cancel" onClick={() => removeDraft(draft.id)}>Delete</button></div></header><div className="experiment-card-summary"><span>Step {draft.step} of 5</span><span>{draft.variants.length} groups</span><span>{leadTotal ? `${leadTotal} leads selected` : "Audience not selected"}</span><span>Testing {dimensionLabel(draft.dimension, draft.dimensionOther)}</span></div></article>;
      }) : <div className="experiment-screen-empty"><strong>No experiment drafts</strong><p>Start a new experiment and choose Save draft whenever you want to continue later.</p></div>}</div> : null}
      {activeScreen === "scheduled" ? <div className="experiment-screen-body">{filteredVisible.length ? filteredVisible.map((item) => { const outcome = experimentOutcomes.get(item.id) ?? { outreached: 0, replied: 0, positive: 0 }; return <article key={item.id}><header><div><span className={`experiment-status ${item.status}`}>{item.status}</span><h3>{item.name}</h3><p>{scheduledLabel(item.scheduled_at, item.timezone)} · {item.timezone}</p><div className="experiment-outcome-summary"><span>{outcome.outreached} properties outreached</span><span>{item.outreach_conversations} conversations</span><span>{outcome.replied} replied</span><span className={outcome.positive > 0 ? "positive" : ""}>{outcome.positive} positive</span></div></div><div><button type="button" onClick={() => { const next = expanded === item.id ? null : item.id; setExpanded(next); onRouteChange?.("launched", next ?? undefined); }}>{expanded === item.id ? "Hide review" : "Review"}</button>{item.status === "scheduled" ? <button className="cancel" type="button" onClick={() => void cancel(item)}>Cancel</button> : null}</div></header>{expanded === item.id ? <div className="experiment-list-detail"><dl><div><dt>Audience</dt><dd>{item.total_leads} leads</dd></div><div><dt>Outreach</dt><dd>{outcome.outreached} properties</dd></div><div><dt>Conversations</dt><dd>{item.outreach_conversations} threads</dd></div><div><dt>Winner</dt><dd>{item.winner_metric.replaceAll("_", " ")}</dd></div><div><dt>Maximum cost</dt><dd>${item.expected_max_cost.toFixed(2)}</dd></div></dl>{outcome.outreached > item.outreach_conversations ? <p className="experiment-conversation-note"><strong>{outcome.outreached} properties are grouped into {item.outreach_conversations} conversations.</strong> Properties with the same owner phone number share one conversation, so no outreach is missing.</p> : null}{item.error ? <p className="experiment-error">{item.error}</p> : null}</div> : null}</article>; }) : <div className="experiment-screen-empty"><strong>{filtersActive ? "No experiments match these filters" : "No launched experiments"}</strong><p>{filtersActive ? "Change or clear the filters to see more experiments." : "Completed drafts appear here after the final review and launch scheduling step."}</p></div>}</div> : null}
    </section>
  </>;
}
