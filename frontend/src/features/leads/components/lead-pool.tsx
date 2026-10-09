"use client";

import { useCallback, useEffect, useMemo, useRef, useState,useReducer } from "react";

import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";

import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession } from "@/features/auth/lib/session-guard";
import { ConfirmDialog, type ConfirmRequest } from "@/components/dialog/confirm-dialog";
import { RetrievalProgress } from "@/components/loading/retrieval-progress";
import { ApiRequestError } from "@/lib/api/http-client";
import type { QueryProgress } from "@/lib/api/query-progress";

import { createCampaignDraft } from "@/features/campaigns/api/campaigns-api";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";

import {
  deleteLeads,
  fetchLeadPool,
  importLeads,
  previewImport,
  repreviewImport,
} from "../api/leads-api";
import type {
  AreaGeometry,
  ImportPreview,
  Lead,
  LeadDetail,
  LeadPoolResponse,
  LeadStage,
  MapBounds,
  MapPoint,
} from "../types/leads.types";
import { ImportDialog } from "./import-dialog";
import { LeadDetailDrawer } from "./lead-detail-drawer";
import {
  LocationFilter,
  NO_LOCATION,
  type LocationSelection,
} from "./location-filter";

import {
  EMPTY_LOCATION_OPTIONS,
  locationFilterReducer,
  EMPTY_LOCATION_FILTER,
  type LocationOptions,
} from "./lead-filterComponents"
import { getFilterData, getLocationBounds, getBoundary, getBoundsFromGeometry, getZipCodes, getAllZipCodes, getCities, getByCity, getByCounty, getByZip, resolveLocation} from "@/lib/location/getstates";
import {
  toStringArray,
  parseAddress,
  LocationCombo,
  PIN_ICON,
  ZIP_ICON,
  CITY_ICON,
} from "./lead-filterComponents";


const EMPTY_POOL: LeadPoolResponse = {
  leads: [],
  map: { mappable_count: 0, pending_count: 0, failed_count: 0 },
  facets: { total: 0, signals: {}, stages: {} },
  locations: { states: [], cities: [], zips: [] },
  signal_catalog: [],
  stage_catalog: [],
};

const LeadMap = dynamic(
  () => import("./lead-map").then((module) => module.LeadMap),
  { ssr: false, loading: () => <div className="leads-map-loading">Loading map…</div> },
);

/** Stages carry meaning, so each gets its own colour rather than one grey chip. */
const STAGE_CLASS: Record<LeadStage, string> = {
  ready: "ready",
  in_campaign: "campaign",
  needs_review: "review",
  dnc: "dnc",
};

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

type StageFilterOption = {
  value: string;
  label: string;
};

function StageFilter({
  value,
  options,
  onChange,
}: {
  value: string;
  options: StageFilterOption[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;

    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };

    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  return (
    <div className={`leads-stage-filter${open ? " open" : ""}`} ref={rootRef}>
      <button
        type="button"
        className="leads-stage-filter-trigger"
        aria-label="Filter by stage"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{selected?.label ?? "All stages"}</span>
        <span className="leads-stage-filter-chevron" aria-hidden="true" />
      </button>
      {open ? (
        <div className="leads-stage-filter-menu" role="listbox" aria-label="Filter by stage">
          {options.map((option) => (
            <button
              key={option.value || "all"}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={option.value === value ? "active" : ""}
              onClick={() => {
                onChange(option.value);
                setOpen(false);
              }}
            >
              <span>{option.label}</span>
              {option.value === value ? <span aria-hidden="true">✓</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function LeadPool({ onDraftCampaign, onOpenConversation }: LeadPoolProps) {
  const [pool, setPool] = useState<LeadPoolResponse>(EMPTY_POOL);
  const [activeSignals, setActiveSignals] = useState<string[]>([]);
  const [stage, setStage] = useState("");
  const [search, setSearch] = useState("");
  /** State / town / ZIP chips. Read out of each address, not stored per lead. */
  const [location, setLocation] = useState<LocationSelection>(NO_LOCATION);
  const [selected, setSelected] = useState<number[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState<QueryProgress<LeadPoolResponse> | null>(null);
  const [hasLoadedPool, setHasLoadedPool] = useState(false);
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
  const [showMap, setShowMap] = useState(false);
  const [focusedLeadId, setFocusedLeadId] = useState<number | null>(null);
  const [mapFocusToken, setMapFocusToken] = useState(0);
  const [hoveredLeadId, setHoveredLeadId] = useState<number | null>(null);
  const [draftMapBounds, setDraftMapBounds] = useState<MapBounds | null>(null);
  const [mapBounds, setMapBounds] = useState<MapBounds | null>(null);
  const [mapPolygon, setMapPolygon] = useState<MapPoint[] | null>(null);
  const [fitMapToken, setFitMapToken] = useState(0);
  const [searchAreaBounds, setSearchAreaBounds] = useState<MapBounds | null>(null);
  const [searchAreaPolygon, setSearchAreaPolygon] = useState<AreaGeometry | null>(null);

  const fileRef = useRef<HTMLInputElement | null>(null);
  const mapSectionRef = useRef<HTMLDivElement | null>(null);
  const dragDepthRef = useRef(0);
  const leadPoolRequestRef = useRef(0);
  const router = useRouter();
  const token = useMemo(() => readAuthSession()?.access_token ?? "", []);

    // ── location filter ────────────────────────────────────────────────────
  // Selected values (state/city/zip/matches/isResolving) live in one reducer.
  // Dropdown option lists live in one merge-updated object. Together these
  // replace the previous nine separate useState hooks.
  const [locationFilter, dispatchLocation] = useReducer(locationFilterReducer, EMPTY_LOCATION_FILTER);
  const [locationOptions, setLocationOptions] = useState<LocationOptions>(EMPTY_LOCATION_OPTIONS);

  function patchOptions(patch: Partial<LocationOptions>) {
    setLocationOptions((current) => ({ ...current, ...patch }));
  }

  // ────────────────────────────────────────────────────────────────────────


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
      const requestId = ++leadPoolRequestRef.current;
      if (!token) {
        setIsLoading(false);
        return;
      }
      setIsLoading(true);
      try {
        const stateCode = locationFilter.state ? locationFilter.state.split(",")[1]?.trim() : "";
        const nextPool = await fetchLeadPool(
          {
            search,
            addresses: locationFilter.addressQuery ? [locationFilter.addressQuery] : [],
            signals: activeSignals,
            stage,
            states: stateCode ? [stateCode] : [],
            // For a geocoded place/alias, filter by the canonical ZIP(s) returned by
            // the resolver. Do not send raw text such as "Brington" as a database city.
            cities: locationFilter.cityKeys.length > 0
              ? locationFilter.cityKeys
              : locationFilter.matches.length === 0 && locationFilter.city
                ? [locationFilter.city]
                : [],
            zips: locationFilter.zips.length > 0
              ? locationFilter.zips
              : locationFilter.cityKeys.length > 0 || locationFilter.addressQuery
                ? []
                : [...new Set(locationFilter.matches.map((item) => item.zip).filter(Boolean))],
            bounds: mapBounds ?? undefined,
            polygon: mapPolygon ?? undefined,
          },
          token,
          signal,
          setLoadProgress,
        );
        if (requestId !== leadPoolRequestRef.current || signal?.aborted) return;
        setPool(nextPool);
        setErrorMessage("");
      } catch (error) {
        if (requestId !== leadPoolRequestRef.current) return;
        handleApiError(error, "Could not load the lead pool.");
      } finally {
        if (requestId === leadPoolRequestRef.current) {
          setIsLoading(false);
          setHasLoadedPool(true);
        }
      }
    },
    [token, search, activeSignals, stage, locationFilter.state, locationFilter.city, locationFilter.cityKeys, locationFilter.addressQuery, locationFilter.zips, locationFilter.matches, mapBounds, mapPolygon, handleApiError],
  );

  // Filters live on the server, so briefly coalesce changes before refetching.
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void refresh(controller.signal), 220);
    return () => {
      window.clearTimeout(timer);
      leadPoolRequestRef.current += 1;
      controller.abort();
    };
  }, [refresh]);

  // Covers every filter — search, stage, signals, AND every piece of the
  // location reducer (state/city/zip/matches). Not location-only. This is
  // the one definition of "a filter is active"; nothing else re-derives it.
  const islocationFiltered = Boolean(
      locationFilter.state ||
      locationFilter.city ||
      locationFilter.addressQuery ||
      locationFilter.zips.length ||
      locationFilter.matches.length,
  );

  // Only the LOCATION portion of filtering happens here (state/city/zip
  // resolved matches, checked against parseAddress on each lead). This does
  // NOT need to check isFiltered internally: when no location filter is
  // active it already returns pool.leads unchanged (same reference, not a
  // copy) via the early return below.
  const filteredLeads = useMemo(() => {
    const { state, zips, matches, addressQuery } = locationFilter;
    if (!state && zips.length === 0 && matches.length === 0) return pool.leads;

    const stateCode = state ? state.split(",")[1]?.trim() : "";
    return pool.leads.filter((lead) => {
      const { city, state: leadState, zip: leadZip } = parseAddress(lead.property_address);

      if (stateCode && leadState !== stateCode) return false;
      if (zips.length > 0 && !zips.includes(leadZip)) return false;

      // Free-typed city search may have resolved to several {city, state, zip}
      // matches (e.g. all 22 Charlestons). A lead passes if it fits ANY of them.
      if (matches.length > 0 && !addressQuery) {
        const matchesAny = matches.some(
          (loc) =>
            (loc.zip && loc.zip === leadZip) ||
            (loc.city && loc.state && loc.city === city && loc.state === leadState),
        );
        if (!matchesAny) return false;
      }

      return true;
    });
  }, [pool.leads,locationFilter]);

  // What actually gets rendered. Not a new array — just a reference to
  // whichever of the two already-computed arrays applies. When isFiltered is
  // false, pool.leads is shown directly; when true, the location-filtered
  // result is shown. No third copy, and no "fall back to everything" branch
  // hiding inside filteredLeads itself.
  const leadsToShow = islocationFiltered ? filteredLeads : pool.leads;

  const visibleIds = leadsToShow.map((lead) => lead.id);
  const selectedVisible = selected.filter((id) => visibleIds.includes(id));
  const allSelected = visibleIds.length > 0 && selectedVisible.length === visibleIds.length;

  function toggleLead(id: number): void {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  function focusLeadOnMap(lead: Lead): void {
    if (lead.latitude == null || lead.longitude == null) {
      setNotice("This lead does not have a mapped location yet.");
      return;
    }
    setFocusedLeadId(lead.id);
    setMapFocusToken((value) => value + 1);
    setShowMap(true);
  }

  // A pin clicked on the map highlights its row without moving the map again.
  function focusLeadFromMap(leadId: number): void {
    setFocusedLeadId(leadId);
  }

  // The focus lapses once filters remove that lead, so the map can fit the new results.
  const activeFocusId = focusedLeadId !== null && leadsToShow.some((lead) => lead.id === focusedLeadId)
    ? focusedLeadId
    : null;

  useEffect(() => {
    if (!showMap || activeFocusId === null) return;
    mapSectionRef.current?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [activeFocusId, mapFocusToken, showMap]);

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
  async function handleRemap(mapping: Record<string, string>, sheet: string): Promise<void> {
    if (!preview) return;
    setIsRemapping(true);
    setMappingError("");
    try {
      setPreview(await repreviewImport(preview.token, mapping, sheet, token));
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
    sheet: string;
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

  // Free-typed city search. The user may type a place that isn't a real city
  // (a neighbourhood, subdivision, etc.), so instead of matching it against
  // the known-cities list we hand the raw text to resolveLocation() and use
  // whatever it resolves to, to drive the filter.
  //
  // A query can be genuinely ambiguous — "Charleston" exists in ~22 states —
  // so resolveLocation() may return several rows for one query. We keep all
  // of them (locationFilter.matches); the filter below matches a lead
  // against ANY of the resolved locations.
  const handleCitySearch = useCallback(
    async (query: string) => {
      dispatchLocation({ type: "RESOLVE_START" });
      setErrorMessage("");
      try {
        const selectedStateName = locationFilter.state
          ? locationFilter.state.split(",")[0].trim()
          : undefined;
        const selectedStateCode = locationFilter.state
          ? locationFilter.state.split(",")[1]?.trim()
          : undefined;

        // Prefer the complete local city index for exact city names. A place
        // geocoder commonly returns one representative point/ZIP, which would
        // incorrectly drop leads in a city's other ZIPs after the API refresh.
        const exactCity = await getByCity(query);
        type ExactCityRecord = {
          city: string;
          state_code?: string;
          state: string;
          zip: string;
        };
        const exactRecords: ExactCityRecord[] = Array.isArray(exactCity.zipcodes)
          ? (exactCity.zipcodes as ExactCityRecord[]).filter((item) =>
              !selectedStateCode || item.state_code === selectedStateCode,
            )
          : [];
        if (exactRecords.length > 0) {
          const exactCityKeys = [...new Set(exactRecords.map((item) => {
            const cityKey = item.city
              .trim()
              .toLowerCase()
              .replace(/^(?:city|town|village|township|borough|municipality)\s+of\s+/, "")
              .replace(/[^a-z0-9]+/g, " ")
              .trim();
            return `${cityKey}|${item.state_code ?? ""}`;
          }))];
          const matches = exactRecords.map((item) => ({
            city: item.city,
            state: item.state_code ?? item.state,
            zip: item.zip,
          }));
          patchOptions({
            states: [...new Set(exactRecords.map((item) =>
              item.state_code ? `${item.state},${item.state_code}` : item.state,
            ))],
            cities: [...new Set(exactRecords.map((item) => item.city))],
            zips: [...new Set(exactRecords.map((item) => item.zip))],
          });
          dispatchLocation({
            type: "RESOLVE_SUCCESS",
            city: query,
            matches,
            cityKeys: exactCityKeys,
          });
          return;
        }

        const exactCounty = await getByCounty(query, selectedStateName);
        const countyRecords: ExactCityRecord[] = Array.isArray(exactCounty.zipcodes)
          ? exactCounty.zipcodes as ExactCityRecord[]
          : [];
        if (countyRecords.length > 0) {
          const matches = countyRecords.map((item) => ({
            city: item.city,
            state: item.state_code ?? item.state,
            zip: item.zip,
          }));
          patchOptions({
            states: [...new Set(countyRecords.map((item) =>
              item.state_code ? `${item.state},${item.state_code}` : item.state,
            ))],
            cities: [...new Set(countyRecords.map((item) => item.city))].sort(),
            zips: [...new Set(countyRecords.map((item) => item.zip))].sort(),
          });
          dispatchLocation({ type: "RESOLVE_SUCCESS", city: query, matches });
          return;
        }

        const result = await resolveLocation(query, selectedStateName);

       if (result?.found && result.data && result.data.length > 0) {
  const matches = result.data.map((item) => ({
    city: item.city,
    state: item.state_code ?? item.state,
    zip: item.zip,
  }));

  patchOptions({
    states: [
      ...new Set(
        result.data.map((item) =>
          item.state_code
            ? `${item.state},${item.state_code}`
            : item.state
        )
      ),
    ],

    cities: [
      ...new Set(
        result.data.map((item) => item.city)
      ),
    ],

    zips: [
      ...new Set(
        result.data
          .map((item) => item.zip)
          .filter(Boolean)
      ),
    ],
  });

  dispatchLocation({
    type: "RESOLVE_SUCCESS",
    city: query,
    matches,
    addressQuery: /(?:^\d+\s|\b(?:street|st|road|rd|avenue|ave|boulevard|blvd|lane|ln|drive|dr|court|ct|highway|hwy)\b)/i.test(query)
      ? query
      : undefined,
  });
}
           else {
          setErrorMessage(`No match found for "${query}".`);
          dispatchLocation({ type: "RESOLVE_FAILURE" });
        }
      } catch (error) {
        dispatchLocation({ type: "RESOLVE_FAILURE" });
        handleApiError(error, "Could not resolve that location.");
      }
    },
    [handleApiError, locationFilter.state],
  );

  // Load states + cities once on mount.
  useEffect(() => {
  getFilterData()
    .then((data) => {
      patchOptions({
        allStates: toStringArray(data.states),
        allCities: toStringArray(data.cities),
      });
    })
    .catch(() => patchOptions({ allStates: [], allCities: [] }));
}, []);

  // When state changes, narrow the zip list to that state.
  useEffect(() => {
    if (!locationFilter.state) return;
    const stateName = locationFilter.state.split(",")[0];
    getZipCodes(stateName)
      .then((data) => patchOptions({ zips: toStringArray(data) }))
      .catch(() => patchOptions({ zips: [] }));
  }, [locationFilter.state]);

  // Narrow the city list when state and/or zip is selected.
  useEffect(() => {
    if (!locationFilter.state && locationFilter.zips.length === 0) return;
    const stateName = locationFilter.state ? locationFilter.state.split(",")[0] : undefined;
    const requests = locationFilter.zips.length > 0
      ? locationFilter.zips.map((zipcode) => getCities({ state: stateName, zipcode }))
      : [getCities({ state: stateName })];
    Promise.all(requests)
      .then((results) => patchOptions({
        cities: [...new Set(results.flatMap((data) => toStringArray(data)))].sort(),
      }))
      .catch(() => patchOptions({ cities: [] }));
  }, [locationFilter.state, locationFilter.zips]);
  
  // Load the full zip list once, on first interaction with the zip field.
  const loadZips = useCallback(() => {
    if (locationOptions.allZips.length > 0) return;
    getAllZipCodes()
      .then((data) => patchOptions({ allZips: toStringArray(data) }))
      .catch(() => patchOptions({ allZips: [] }));
  }, [locationOptions.allZips]);

  // Narrows states + zips when a city is picked. getByCity() is a case-
// insensitive exact match, so ambiguous cities (Charleston, ~22 states)
// come back with every matching state/zip already — no extra filtering here.
// Clearing city empties these, and the fallbacks below drop back to
// allStates/allZips — that IS the "show everything" reset, nothing extra needed.
useEffect(() => {
  // City was cleared.
  if (!locationFilter.city) {
    // If a state is still selected, restore ALL cities + ZIPs for that state.
    if (locationFilter.state) {
      const stateName = locationFilter.state.split(",")[0].trim();

      Promise.all([
        getCities({ state: stateName }),
        getZipCodes(stateName),
      ])
        .then(([citiesData, zipsData]) => {
          patchOptions({
            cities: toStringArray(citiesData),
            zips: toStringArray(zipsData),
          });
        })
        .catch(() => {
          patchOptions({
            cities: [],
            zips: [],
          });
        });

      return;
    }

    // If only ZIP is selected, let ZIP -> city/state logic handle it.
    if (locationFilter.zips.length > 0) {
      return;
    }

    // Nothing selected: go back to full lists on the next task so this effect
    // does not synchronously cascade another render.
    const resetTimer = window.setTimeout(() => {
      patchOptions({
        states: [],
        cities: [],
        zips: [],
      });
    }, 0);

    return () => window.clearTimeout(resetTimer);
  }

  // A geocoded/free-typed place already has canonical results.
  // Do not overwrite them using exact CSV city matching.
  if (locationFilter.matches.length > 0) {
    return;
  }

  // Normal exact city selection.
  getByCity(locationFilter.city)
    .then((data) => {
      patchOptions({
        states: toStringArray(data.states),
        zips: toStringArray(data.zipcodes ?? []),
      });
    })
    .catch(() => {
      patchOptions({
        states: [],
        zips: [],
      });
    });
}, [
  locationFilter.city,
  locationFilter.state,
  locationFilter.zips,
  locationFilter.matches.length,
]);
  // ZIP -> State + City. This completes the dependency graph in the other
  // direction, instead of only supporting State -> ZIP/City.
  useEffect(() => {
    if (locationFilter.zips.length === 0) return;

    Promise.all(locationFilter.zips.map((zip) => getByZip(zip)))
      .then((results) => {
        patchOptions({
          states: [...new Set(results.flatMap((data) => toStringArray(data.states)))].sort(),
          cities: [...new Set(results.flatMap((data) => toStringArray(data.cities)))].sort(),
        });
      })
      .catch(() => patchOptions({ states: [], cities: [] }));
  }, [locationFilter.zips]);

  const availableStates =
    locationFilter.city || locationFilter.zips.length > 0
      ? locationOptions.states
      : locationOptions.allStates;

const availableZip =
  locationFilter.state || locationFilter.city ? locationOptions.zips : locationOptions.allZips;

const availableCities =
  locationFilter.state || locationFilter.zips.length > 0 ? locationOptions.cities : locationOptions.allCities;

const comboStateName = locationFilter.state.split(",")[0]?.trim() || null;
const searchStateCode =
  location.states.length === 1 && location.cities.length === 0 && location.zips.length === 0
    ? location.states[0].trim().toUpperCase()
    : null;
const searchStateName = searchStateCode
  ? locationOptions.allStates
      .find((item) => item.split(",")[1]?.trim().toUpperCase() === searchStateCode)
      ?.split(",")[0]
      ?.trim() || null
  : null;
const fallbackStateAreaName =
  comboStateName &&
  !locationFilter.city &&
  locationFilter.zips.length === 0 &&
  locationFilter.matches.length === 0
    ? comboStateName
    : searchStateName;

useEffect(() => {
  const hasLocationArea = Boolean(
    locationFilter.zips.length > 0 || locationFilter.city || fallbackStateAreaName,
  );
  if (!hasLocationArea) {
    const clearTimer = window.setTimeout(() => {
      setSearchAreaBounds(null);
      setSearchAreaPolygon(null);
    }, 0);
    return () => window.clearTimeout(clearTimer);
  }

  let cancelled = false;
  const loadAreaRecords = async (): Promise<unknown[]> => {
    if (locationFilter.zips.length > 0) {
      const results = await Promise.all(locationFilter.zips.map((zip) => getByZip(zip)));
      return results.flatMap((data) => Array.isArray(data.zipcodes) ? data.zipcodes : []);
    }

    if (locationFilter.city) {
      const resolvedZipCodes = [...new Set(
        locationFilter.matches.map((item) => item.zip).filter(Boolean),
      )];
      if (resolvedZipCodes.length > 0) {
        const resolvedMatches = await Promise.all(
          resolvedZipCodes.map((zipCode) => getByZip(zipCode)),
        );
        return resolvedMatches.flatMap((item) =>
          Array.isArray(item.zipcodes) ? item.zipcodes : [],
        );
      }

      const cityData = await getByCity(locationFilter.city);
      if (Array.isArray(cityData.zipcodes) && cityData.zipcodes.length > 0) {
        return cityData.zipcodes;
      }
      return [];
    }

    return fallbackStateAreaName ? getZipCodes(fallbackStateAreaName) : [];
  };

  void loadAreaRecords()
    .then(async (data) => {
      if (cancelled) return;

      const zipCodes = Array.isArray(data)
        ? data
            .map((item) => (item as { zip?: string })?.zip)
            .filter((zip): zip is string => Boolean(zip))
        : [];

      const geometry = zipCodes.length > 0 ? await getBoundary(zipCodes) : null;
      if (cancelled) return;

      if (geometry) {
        setSearchAreaBounds(getBoundsFromGeometry(geometry));
        setSearchAreaPolygon(geometry);
        return;
      }

      // No real boundary on file (e.g. state-level fallback) - fall back to
      // the centroid bounding box / hull so the map still frames the area.
      setSearchAreaBounds(getLocationBounds(data));
      setSearchAreaPolygon(null);
    })
    .catch(() => {
      if (!cancelled) {
        setSearchAreaBounds(null);
        setSearchAreaPolygon(null);
      }
    });
  return () => {
    cancelled = true;
  };
}, [fallbackStateAreaName, locationFilter.city, locationFilter.zips, locationFilter.matches]);

    const isFiltered = Boolean(stage || activeSignals.length || islocationFiltered || mapBounds || mapPolygon);
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
                (isFiltered ? ` · ${leadsToShow.length} shown` : "")}
          </p>
        </div>
        <div className="dashboard-header-actions">
          <NotificationBell />
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
        </div>
      </header>

      <div className="leads-toolbar">
        <StageFilter
          value={stage}
          onChange={setStage}
          options={[
            { value: "", label: "All stages" },
            ...pool.stage_catalog
              .filter((item) => item.key !== "needs_review" && item.key !== "in_campaign")
              .map((item) => ({
                value: item.key,
                label: `${item.label}${pool.facets.stages[item.key] ? ` (${pool.facets.stages[item.key]})` : ""}`,
              })),
          ]}
        />
        <LocationCombo
  icon={PIN_ICON}
  ariaLabel="Filter by state"
  placeholder="State…"
  options={availableStates}
  selected={locationFilter.state}
  onSelect={(value) => dispatchLocation({ type: "SET_STATE", value })}
  onClear={() => dispatchLocation({ type: "CLEAR_STATE" })}
/>
        <LocationCombo
          icon={CITY_ICON}
          ariaLabel="Filter by city"
          placeholder={locationFilter.isResolving ? "Searching…" : "City, street or county…"}
          options={availableCities}
          selected={locationFilter.city}
          onSelect={handleCitySearch}
          onClear={() => dispatchLocation({ type: "CLEAR_CITY" })}
          onSubmit={handleCitySearch}
          optionLimit={locationFilter.state || locationFilter.zips.length > 0 ? undefined : 50}
        />

        <LocationCombo
          icon={ZIP_ICON}
          ariaLabel="Filter by ZIP code"
          placeholder="ZIP…"
          options={availableZip}
          selected={locationFilter.zips}
          onSelect={(value) => dispatchLocation({ type: "SET_ZIP", value })}
          onClear={(value) => {
            if (value) dispatchLocation({ type: "REMOVE_ZIP", value });
            else dispatchLocation({ type: "CLEAR_ZIPS" });
          }}
          onClick={() => loadZips()}
          optionLimit={locationFilter.state || locationFilter.city ? undefined : 50}
        />

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
                setMapBounds(null);
                setMapPolygon(null);
               
                dispatchLocation({ type: "CLEAR_ALL" });
              }}
            >
              Clear filters
            </button>
          ) : null}
        </div>
      </div>

      <div className="leads-viewbar">
        <div className="leads-view-toggle" role="group" aria-label="Map visibility">
          <button
            type="button"
            className={showMap ? "on" : undefined}
            aria-pressed={showMap}
            onClick={() => setShowMap((visible) => !visible)}
          >
            {showMap ? "Hide map" : "Show map"}
          </button>
        </div>
        {showMap ? (
          <div className="leads-map-actions">
            <span className={mapBounds || mapPolygon ? "leads-map-count active" : "leads-map-count"}>
              {mapPolygon
                ? `${leadsToShow.length} ${leadsToShow.length === 1 ? "lead" : "leads"} in polygon`
                : mapBounds
                ? `${leadsToShow.length} ${leadsToShow.length === 1 ? "lead" : "leads"} in map area`
                : `${pool.map.mappable_count} mapped ${pool.map.mappable_count === 1 ? "lead" : "leads"}`}
            </span>
            {pool.map.pending_count > 0 ? (
              <span className="leads-map-waiting">{pool.map.pending_count} waiting for geocoding</span>
            ) : null}
            <button
              type="button"
              className="sms-button-secondary"
              disabled={!draftMapBounds}
              onClick={() => {
                if (!draftMapBounds) return;
                setMapPolygon(null);
                setMapBounds(draftMapBounds);
              }}
            >
              Apply map area
            </button>
            {mapBounds ? (
              <button type="button" className="leads-clear" onClick={() => setMapBounds(null)}>
                Clear map filter
              </button>
            ) : null}
            <button
              type="button"
              className="leads-clear"
              onClick={() => {
                setFocusedLeadId(null);
                setFitMapToken((value) => value + 1);
              }}
            >
              {searchAreaBounds ? "Fit search area" : "Fit results"}
            </button>
          </div>
        ) : mapBounds || mapPolygon ? (
          <button
            type="button"
            className="leads-map-filter-pill"
            onClick={() => { setMapBounds(null); setMapPolygon(null); }}
          >
            Geographic filter active · Clear
          </button>
        ) : null}
      </div>

      {showMap ? (
        <div ref={mapSectionRef}>
          <LeadMap
            leads={leadsToShow}
            selectedIds={selected}
            focusLeadId={activeFocusId}
            focusToken={mapFocusToken}
            hoveredLeadId={hoveredLeadId}
            fitToken={fitMapToken}
            searchAreaBounds={searchAreaBounds}
            searchAreaPolygon={searchAreaPolygon}
            onToggleLead={toggleLead}
            onViewLead={setDetailId}
            onFocusLead={focusLeadFromMap}
            onBoundsChange={setDraftMapBounds}
            activePolygon={mapPolygon}
            onPolygonApply={(polygon) => {
              setMapBounds(null);
              setMapPolygon(polygon);
            }}
            onPolygonClear={() => setMapPolygon(null)}
          />
        </div>
      ) : null}

      {selectedVisible.length > 0 ? (
        <div className="leads-selection-bar" role="status">
          <strong>
            {selectedVisible.length} {selectedVisible.length === 1 ? "lead" : "leads"} selected
          </strong>
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
              disabled={isDrafting}
            >
              {isDrafting ? "Opening…" : "✦ Create campaign"}
            </button>
          </div>
        </div>
      ) : null}

      <div
        className={`leads-table-wrap${isLoading ? " is-loading" : ""}${
          isLoading && !hasLoadedPool ? " initial-load" : ""
        }`}
        aria-busy={isLoading}
      >
        {isLoading ? (
          <RetrievalProgress
            eyebrow="LEAD POOL"
            title="Retrieving leads"
            description="Finding matching leads and preparing your Lead Pool."
            status="Fetching leads for you"
            detail={hasLoadedPool
              ? `${leadsToShow.length.toLocaleString()} current ${leadsToShow.length === 1 ? "lead remains" : "leads remain"} visible`
              : "Preparing leads, filters, and map totals"}
            ariaLabel={hasLoadedPool ? "Refreshing lead pool" : "Loading lead pool"}
            progressLabel="Lead pool database query"
            progress={loadProgress}
            progressUnit="records"
          />
        ) : null}
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
              <th scope="col">Source</th>
              <th scope="col"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
           {leadsToShow.map((lead) => {
              const isChecked = selected.includes(lead.id);
              return (
                <tr
                  key={lead.id}
                  className={`${isChecked ? "selected " : ""}${activeFocusId === lead.id ? "map-focused" : ""}`.trim() || undefined}
                  onMouseEnter={() => setHoveredLeadId(lead.id)}
                  onMouseLeave={() => setHoveredLeadId((current) => (current === lead.id ? null : current))}
                >
                  <td className="leads-select-cell">
                    <input
                      type="checkbox"
                      checked={isChecked}
                      onChange={() => toggleLead(lead.id)}
                      aria-label={`Select ${lead.owner_name || lead.phone || "lead"}`}
                    />
                  </td>
                  <td>
                    <button
                      type="button"
                      className="leads-owner"
                      onClick={() => focusLeadOnMap(lead)}
                      title={lead.latitude == null || lead.longitude == null ? "Map location pending" : "Locate this lead on the map"}
                    >
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
                          <span key={signal.key} className="leads-signal">
                            {signal.label}
                          </span>
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

        {!isLoading && leadsToShow.length === 0 ? (
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

      {errorMessage ? (
        <div className="sms-toast error leads-error-toast" role="alert">
          <span>{errorMessage}</span>
          <button type="button" onClick={() => setErrorMessage("")} aria-label="Dismiss error">×</button>
        </div>
      ) : null}
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
        onRemap={(mapping, sheet) => void handleRemap(mapping, sheet)}
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
