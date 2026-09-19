"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession } from "@/features/auth/lib/session-guard";
import { ConfirmDialog, type ConfirmRequest } from "@/components/dialog/confirm-dialog";
import { ApiRequestError } from "@/lib/api/http-client";

import { createCampaignDraft } from "@/features/campaigns/api/campaigns-api";

import {
  deleteLeads,
  fetchLeadPool,
  importLeads,
  previewImport,
  repreviewImport,
} from "../api/leads-api";
import type {
  ImportPreview,
  Lead,
  LeadDetail,
  LeadPoolResponse,
  LeadStage,
} from "../types/leads.types";
import { ImportDialog } from "./import-dialog";
import { LeadDetailDrawer } from "./lead-detail-drawer";
import {
  LocationFilter,
  NO_LOCATION,
  locationCount,
  type LocationSelection,
} from "./location-filter";

const EMPTY_POOL: LeadPoolResponse = {
  leads: [],
  facets: { total: 0, signals: {}, stages: {} },
  locations: { states: [], cities: [], zips: [] },
  signal_catalog: [],
  stage_catalog: [],
};

/** Stages carry meaning, so each gets its own colour rather than one grey chip. */
const STAGE_CLASS: Record<LeadStage, string> = {
  ready: "ready",
  in_campaign: "campaign",
  needs_review: "review",
  dnc: "dnc",
};

/** Mirrors MAX_CAMPAIGN_SIZE on the server, so the limit is visible before you click. */
const MAX_CAMPAIGN_SIZE = 200;

/** Score bands match the review queue: strong, worth a look, everything else. */
function scoreClass(score: number): string {
  if (score >= 80) return "strong";
  if (score >= 70) return "fair";
  return "plain";
}

function relativeTime(value: string | null): string {
  if (!value) return "Just added";
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(stamp.getTime())) return "Just added";

  const minutes = Math.round((Date.now() - stamp.getTime()) / 60000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days} ${days === 1 ? "day" : "days"} ago`;
  return stamp.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

type LeadPoolProps = {
  /**
   * Hands a freshly drafted campaign to the Campaigns tab, where the broker
   * names it and writes the message. Nothing is sent from the pool.
   */
  onDraftCampaign?: (campaignId: number) => void;
  /** Opens one thread in Follow-ups, for a lead that is already in a campaign. */
  onOpenConversation?: (conversationId: number) => void;
};

export function LeadPool({ onDraftCampaign, onOpenConversation }: LeadPoolProps) {
  const [pool, setPool] = useState<LeadPoolResponse>(EMPTY_POOL);
  const [activeSignals, setActiveSignals] = useState<string[]>([]);
  const [stage, setStage] = useState("");
  const [search, setSearch] = useState("");
  /** State / town / ZIP chips. Read out of each address, not stored per lead. */
  const [location, setLocation] = useState<LocationSelection>(NO_LOCATION);
  const [selected, setSelected] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isImporting, setIsImporting] = useState(false);
  /** The chosen file waiting on the import options, and its dry-run counts. */
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [isRemapping, setIsRemapping] = useState(false);
  const [mappingError, setMappingError] = useState("");
  const [isDrafting, setIsDrafting] = useState(false);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  /** The lead whose details panel is open, or null. */
  const [detailId, setDetailId] = useState<number | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [isDraggingFile, setIsDraggingFile] = useState(false);

  const fileRef = useRef<HTMLInputElement | null>(null);
  const dragDepthRef = useRef(0);
  const router = useRouter();
  const token = useMemo(() => readAuthSession()?.access_token ?? "", []);

  const handleApiError = useCallback(
    (error: unknown, fallback: string) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiRequestError && error.status === 401) {
        endSession();
        router.replace("/login?expired=1");
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : fallback);
    },
    [router],
  );

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      try {
        setPool(
          await fetchLeadPool(
            {
              search,
              signals: activeSignals,
              stage,
              states: location.states,
              cities: location.cities,
              zips: location.zips,
            },
            token,
            signal,
          ),
        );
        setErrorMessage("");
      } catch (error) {
        handleApiError(error, "Could not load the lead pool.");
      } finally {
        setIsLoading(false);
      }
    },
    [token, search, activeSignals, stage, location, handleApiError],
  );

  // Filters live on the server, so briefly coalesce changes before refetching.
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void refresh(controller.signal), 220);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [refresh]);

  const visibleIds = pool.leads.map((lead) => lead.id);
  const selectedVisible = selected.filter((id) => visibleIds.includes(id));
  const allSelected = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;

  function toggleLead(id: number): void {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  function toggleAll(): void {
    setSelected((current) =>
      allSelected ? current.filter((id) => !visibleIds.includes(id)) : [...new Set([...current, ...visibleIds])],
    );
  }

  function toggleSignal(key: string): void {
    setActiveSignals((current) =>
      current.includes(key) ? current.filter((item) => item !== key) : [...current, key],
    );
  }

  /** Choosing or dropping a file only opens the options; nothing is written until confirmed. */
  async function prepareImport(file: File | undefined): Promise<void> {
    if (!file) return;

    const extension = file.name.toLowerCase().split(".").pop();
    if (extension !== "csv" && extension !== "xlsx") {
      setErrorMessage("Upload a .csv or .xlsx file.");
      return;
    }

    setPendingFile(file);
    setPreview(null);
    setErrorMessage("");
    setMappingError("");
    try {
      setPreview(await previewImport(file, token));
    } catch (error) {
      setPendingFile(null);
      handleApiError(error, "The file could not be read.");
    }
  }

  async function handleFileChosen(event: React.ChangeEvent<HTMLInputElement>): Promise<void> {
    const file = event.target.files?.[0];
    // Reset immediately so re-choosing the same file still fires a change event.
    event.target.value = "";
    await prepareImport(file);
  }

  function handleDragEnter(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    event.stopPropagation();
    if (isImporting || pendingFile !== null) return;
    dragDepthRef.current += 1;
    setIsDraggingFile(true);
  }

  function handleDragLeave(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
    if (dragDepthRef.current === 0) setIsDraggingFile(false);
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>): void {
    event.preventDefault();
    event.stopPropagation();
    event.dataTransfer.dropEffect = isImporting || pendingFile !== null ? "none" : "copy";
  }

  async function handleDrop(event: React.DragEvent<HTMLDivElement>): Promise<void> {
    event.preventDefault();
    event.stopPropagation();
    dragDepthRef.current = 0;
    setIsDraggingFile(false);
    if (isImporting || pendingFile !== null) return;
    await prepareImport(event.dataTransfer.files?.[0]);
  }

  /** Re-runs the dry run when the broker corrects a column. */
  async function handleRemap(mapping: Record<string, string>): Promise<void> {
    if (!preview) return;
    setIsRemapping(true);
    setMappingError("");
    try {
      setPreview(await repreviewImport(preview.token, mapping, token));
    } catch (error) {
      // The file is still staged, so this is recoverable — keep the dialog open.
      setMappingError(error instanceof Error ? error.message : "That mapping cannot be used.");
    } finally {
      setIsRemapping(false);
    }
  }

  async function handleImport(options: {
    limit: number;
    mapping: Record<string, string>;
  }): Promise<void> {
    if (!pendingFile || !preview) return;
    setIsImporting(true);
    try {
      // The preview already put the file on the server; send its token, not the file.
      const result = await importLeads(preview.token, options, token);
      setNotice(
        `Imported ${pendingFile.name}: ${result.created} ` +
          `${result.created === 1 ? "lead" : "leads"} added.`,
      );
      if (result.warnings.length) setErrorMessage(result.warnings.slice(0, 3).join(" "));
      setPendingFile(null);
      setPreview(null);
      await refresh();
    } finally {
      setIsImporting(false);
    }
  }

  /**
   * Drafts a campaign from the selection and moves the broker to the Campaigns
   * tab to write it. This sends nothing: the message is composed there, against
   * a preview of what each owner would actually read.
   */
  async function handleDraftCampaign(leadIds: number[]): Promise<void> {
    if (isDrafting || leadIds.length === 0) return;
    setIsDrafting(true);
    setErrorMessage("");
    try {
      const result = await createCampaignDraft(leadIds, token);
      setSelected([]);
      if (result.not_added.length) {
        // Say so before the composer opens with fewer rows than were selected.
        setErrorMessage(
          `${result.not_added.length} of the selected leads were left out: ` +
            result.not_added
              .slice(0, 3)
              .map((item) => `${item.owner_name ?? `Lead ${item.lead_id}`} — ${item.reason}`)
              .join(" "),
        );
      }
      await refresh();
      onDraftCampaign?.(result.campaign.id);
    } catch (error) {
      handleApiError(error, "Could not start a campaign from that selection.");
    } finally {
      setIsDrafting(false);
    }
  }

  /** Shared by the row bin and the selection bar; ids are always plural here. */
  async function removeLeads(ids: number[]): Promise<void> {
    const result = await deleteLeads(ids, token);
    setSelected((current) => current.filter((id) => !ids.includes(id)));
    setNotice(`Removed ${result.deleted} ${result.deleted === 1 ? "lead" : "leads"} from the pool.`);
    await refresh();
  }

  function confirmRemoveOne(lead: Lead | LeadDetail): void {
    const who = lead.owner_name || lead.phone || "this lead";
    setConfirmRequest({
      title: `Remove ${who}?`,
      body:
        lead.conversation_id === null
          ? "The lead is removed from your pool. Re-importing the same list brings it back."
          : "The lead leaves your pool. The conversation Bobbie already started stays in Follow-ups.",
      confirmLabel: "Remove lead",
      tone: "danger",
      onConfirm: async () => {
        await removeLeads([lead.id]);
        if (detailId === lead.id) setDetailId(null);
      },
    });
  }

  function confirmRemoveSelected(): void {
    const count = selectedVisible.length;
    const inCampaign = pool.leads.filter(
      (lead) => selectedVisible.includes(lead.id) && lead.conversation_id !== null,
    ).length;
    setConfirmRequest({
      title: `Remove ${count} ${count === 1 ? "lead" : "leads"}?`,
      body:
        `${count === 1 ? "This lead is" : "These leads are"} removed from your pool. ` +
        (inCampaign
          ? `${inCampaign} of them already ${inCampaign === 1 ? "has a" : "have"} conversation${
              inCampaign === 1 ? "" : "s"
            } in Follow-ups, which will not be deleted.`
          : "Re-importing the same list brings them back."),
      confirmLabel: `Remove ${count === 1 ? "lead" : "leads"}`,
      tone: "danger",
      onConfirm: () => removeLeads(selectedVisible),
    });
  }

  const isFiltered = Boolean(search.trim() || stage || activeSignals.length || locationCount(location));
  const stageLabels = Object.fromEntries(pool.stage_catalog.map((item) => [item.key, item.label]));

  return (
    <section className="leads-pool">
      <header className="leads-header">
        <div>
          <span className="sms-eyebrow">PROSPECTING</span>
          <div className="leads-title-row">
            <h1 className="view-title">Lead Pool</h1>
            <LocationFilter
              facets={pool.locations}
              selection={location}
              onChange={setLocation}
              advancedSearch={search}
              onAdvancedSearchChange={setSearch}
            />
          </div>
          <p>
            {pool.facets.total === 0
              ? "Import a CSV or Excel file to start building your pool."
              : `${pool.facets.total} ${pool.facets.total === 1 ? "lead" : "leads"} in your market area` +
                (isFiltered ? ` · ${pool.leads.length} shown` : "")}
          </p>
        </div>
        <div
          className={`leads-import-dropzone${isDraggingFile ? " dragging" : ""}${
            isImporting || pendingFile !== null ? " disabled" : ""
          }`}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={(event) => void handleDrop(event)}
        >
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="sr-only"
            onChange={handleFileChosen}
          />
          <div className="leads-import-dropzone-copy">
            <strong>{isDraggingFile ? "Drop file to preview" : "Drag & drop CSV or Excel"}</strong>
            <span>.csv or .xlsx</span>
          </div>
          <button
            className="button"
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={isImporting || pendingFile !== null}
          >
            {isImporting ? "Importing…" : "Choose file"}
          </button>
        </div>
      </header>

      <div className="leads-toolbar">
        <label className="leads-stage-filter">
          <span className="sr-only">Filter by stage</span>
          <select value={stage} onChange={(event) => setStage(event.target.value)}>
            <option value="">All stages</option>
            {pool.stage_catalog
              .filter((item) => item.key !== "needs_review" && item.key !== "in_campaign")
              .map((item) => (
                <option key={item.key} value={item.key}>
                  {item.label}
                  {pool.facets.stages[item.key] ? ` (${pool.facets.stages[item.key]})` : ""}
                </option>
              ))}
          </select>
        </label>

        <div className="leads-signal-filters" role="group" aria-label="Filter by signal">
          {pool.signal_catalog
            .filter((item) => (pool.facets.signals[item.key] ?? 0) > 0)
            .map((item) => (
              <button
                key={item.key}
                type="button"
                className={`leads-chip${activeSignals.includes(item.key) ? " on" : ""}`}
                aria-pressed={activeSignals.includes(item.key)}
                onClick={() => toggleSignal(item.key)}
              >
                {item.label}
                <span className="leads-chip-count">{pool.facets.signals[item.key]}</span>
              </button>
            ))}
          {isFiltered ? (
            <button
              type="button"
              className="leads-clear"
              onClick={() => {
                setSearch("");
                setStage("");
                setActiveSignals([]);
                setLocation(NO_LOCATION);
              }}
            >
              Clear filters
            </button>
          ) : null}
        </div>
      </div>

      {selectedVisible.length > 0 ? (
        <div className="leads-selection-bar" role="status">
          <strong>
            {selectedVisible.length} {selectedVisible.length === 1 ? "lead" : "leads"} selected
          </strong>
          {selectedVisible.length > MAX_CAMPAIGN_SIZE ? (
            <span className="leads-selection-warning">
              A campaign can hold at most {MAX_CAMPAIGN_SIZE} — narrow the selection to text these.
            </span>
          ) : null}
          <button type="button" className="leads-clear" onClick={() => setSelected([])}>
            Clear selection
          </button>
          <div className="leads-selection-actions">
            <button type="button" className="sms-button-secondary" onClick={confirmRemoveSelected}>
              Remove
            </button>
            <button
              className="button"
              type="button"
              onClick={() => void handleDraftCampaign(selectedVisible)}
              disabled={isDrafting || selectedVisible.length > MAX_CAMPAIGN_SIZE}
              title={
                selectedVisible.length > MAX_CAMPAIGN_SIZE
                  ? `A campaign can hold at most ${MAX_CAMPAIGN_SIZE} leads.`
                  : undefined
              }
            >
              {isDrafting ? "Opening…" : "✦ Create campaign"}
            </button>
          </div>
        </div>
      ) : null}

      <div className="leads-table-wrap">
        <table className="leads-table">
          <thead>
            <tr>
              <th scope="col" className="leads-select-cell">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleAll}
                  disabled={visibleIds.length === 0}
                  aria-label="Select all leads shown"
                />
              </th>
              <th scope="col">Owner / Property</th>
              <th scope="col">Signals</th>
              <th scope="col" className="leads-numeric leads-score-header">
                <span>Score</span>
                <span className="leads-score-help">
                  <button
                    type="button"
                    aria-label="About ListingIQ lead scores"
                    aria-describedby="leads-score-explanation"
                  >
                    ?
                  </button>
                  <span id="leads-score-explanation" role="tooltip">
                    This is ListingIQ’s estimated lead score based on the signals available for this record.
                  </span>
                </span>
              </th>
              <th scope="col">Stage</th>
              <th scope="col">Last activity</th>
              <th scope="col">Phone</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {pool.leads.map((lead) => {
              const isChecked = selected.includes(lead.id);
              return (
                <tr key={lead.id} className={isChecked ? "selected" : undefined}>
                  <td className="leads-select-cell">
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggleLead(lead.id)}
                      aria-label={`Select ${lead.owner_name || lead.phone || "lead"}`}
                    />
                  </td>
                  <td>
                    <button type="button" className="leads-owner" onClick={() => setDetailId(lead.id)}>
                      {lead.owner_name || lead.phone || "Unnamed owner"}
                    </button>
                    <span className="leads-address">
                      {lead.property_address || "No address on file"}
                      {lead.area ? ` · ${lead.area}` : ""}
                    </span>
                  </td>
                  <td>
                    <span className="leads-signals">
                      {lead.signals.length === 0 ? (
                        <span className="leads-none">—</span>
                      ) : (
                        lead.signals.map((signal) => (
                          <span key={signal.key} className="leads-signal">{signal.label}</span>
                        ))
                      )}
                    </span>
                  </td>
                  <td className="leads-numeric">
                    <span className={`leads-score ${scoreClass(lead.score)}`}>{lead.score}</span>
                  </td>
                  <td>
                    <span className={`leads-stage ${STAGE_CLASS[lead.stage]}`}>
                      {stageLabels[lead.stage] ?? lead.stage}
                    </span>
                  </td>
                  <td className="leads-activity">{relativeTime(lead.last_activity_at)}</td>
                  <td className="leads-phone-cell">
                    {lead.phone_numbers.length > 0 ? (
                      <span className="leads-phone-list">
                        {lead.phone_numbers.map((number) => (
                          <span className="leads-phone-entry" key={number.phone}>
                            <a href={`tel:${number.phone}`}>{number.phone}</a>
                            {number.dnc ? <span className="leads-phone-dnc">DNC</span> : null}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="leads-none">No number</span>
                    )}
                  </td>
                  <td className="leads-row-actions">
                    <button
                      type="button"
                      className="sms-icon-button"
                      onClick={() => setDetailId(lead.id)}
                      aria-label={`View details for ${lead.owner_name || "lead"}`}
                      title="View property details"
                    >
                      ⓘ
                    </button>
                    <button
                      type="button"
                      className="sms-icon-button"
                      onClick={() => confirmRemoveOne(lead)}
                      aria-label={`Remove ${lead.owner_name || "lead"}`}
                      title="Remove from pool"
                    >
                      🗑
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {isLoading ? <p className="sms-muted">Loading leads…</p> : null}
        {!isLoading && pool.leads.length === 0 ? (
          <div className="leads-empty">
            <div aria-hidden="true">◎</div>
            <h3>{pool.facets.total === 0 ? "No leads yet" : "Nothing matches those filters"}</h3>
            <p>
              {pool.facets.total === 0
                ? "Upload a .csv or .xlsx file with owner name, phone, property address and signal columns. Column names are matched loosely, so most vendor exports work as-is."
                : stage === "in_campaign"
                  ? "Leads handed to a campaign are listed under that campaign, in the Campaigns tab."
                  : "Try a different location, stage, or signal."}
            </p>
          </div>
        ) : null}
      </div>

      {errorMessage ? <p className="sms-toast error" role="alert">{errorMessage}</p> : null}
      {notice ? (
        <p className="sms-toast" role="status" onAnimationEnd={() => setNotice("")}>
          {notice}
        </p>
      ) : null}

      <LeadDetailDrawer
        leadId={detailId}
        accessToken={token}
        stageLabels={stageLabels}
        onClose={() => setDetailId(null)}
        onOpenConversation={(conversationId) => {
          setDetailId(null);
          onOpenConversation?.(conversationId);
        }}
      />

      <ImportDialog
        file={pendingFile}
        preview={preview}
        isImporting={isImporting}
        isRemapping={isRemapping}
        mappingError={mappingError}
        onRemap={(mapping) => void handleRemap(mapping)}
        onClose={() => {
          setPendingFile(null);
          setPreview(null);
          setMappingError("");
        }}
        onConfirm={handleImport}
      />

      <ConfirmDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />
    </section>
  );
}
