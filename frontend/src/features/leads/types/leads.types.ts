/** A signal is why a property is worth a conversation (FSBO, probate, …). */
export type LeadSignal = {
  key: string;
  label: string;
};

/** Stages are derived by the backend; the broker never sets one by hand. */
export type LeadStage = "dnc" | "in_campaign" | "needs_review" | "ready";

export type Lead = {
  id: number;
  owner_name: string | null;
  phone: string | null;
  phone_numbers: { phone: string; dnc: boolean }[];
  property_address: string | null;
  area: string | null;
  signals: LeadSignal[];
  score: number;
  /** The reason shipped with the lead, if the import carried one. */
  outreach_reason: string | null;
  stage: LeadStage;
  last_activity_at: string | null;
  conversation_id: number | null;
  created_at: string | null;
};

/** The extra facts the details drawer shows on top of a pool row. */
export type LeadDetail = Lead & {
  refreshed_at: string | null;
  /** Property attributes from the import: beds, baths, price, … */
  details: Record<string, string>;
  score_breakdown: { label: string; points: number }[];
  conversation: {
    id: number;
    handled_by: "bobbie" | "broker";
    lead_status: string;
    meeting_booked: boolean;
    message_count: number;
    latest_message: string | null;
    latest_message_at: string | null;
  } | null;
};

/** One choice in the location filter, with how many leads it would show. */
export type LocationOption = {
  /** What the API expects back: "IL", "mattoon|IL", "61938". */
  key: string;
  label: string;
  /** Counted under the other two location filters, not this one's own. */
  count: number;
};

/** The location filter menu, rebuilt on every request as the filters narrow. */
export type LocationFacets = {
  states: LocationOption[];
  cities: LocationOption[];
  zips: LocationOption[];
};

export type LeadFacets = {
  total: number;
  signals: Record<string, number>;
  stages: Record<string, number>;
};

export type LeadPoolResponse = {
  leads: Lead[];
  facets: LeadFacets;
  locations: LocationFacets;
  signal_catalog: LeadSignal[];
  stage_catalog: { key: LeadStage; label: string }[];
};

export type ImportPreview = {
  /** Identifies the file already staged on the server, so it uploads once. */
  token: string;
  /** Every column in the file, for the mapping dropdowns. */
  columns: string[];
  /** Which column was used for each field; null means none was found. */
  mapping: Record<string, string | null>;
  /** Columns treated as yes/no signal flags, keyed by signal. */
  signal_columns: Record<string, string>;
  total_rows: number;
  /** Readable rows remaining after existing and repeated phone numbers are removed. */
  importable: number;
  duplicate_count: number;
  warnings: string[];
  sample: {
    owner_name: string | null;
    phone: string | null;
    property_address: string | null;
    area: string | null;
    signals: string[];
    outreach_reason: string | null;
    details: Record<string, string>;
  }[];
};

export type LeadImportResult = {
  created: number;
  total_rows: number;
  warnings: string[];
};

/** One entry in a lead's timeline — a stage move, assignment change, AI/agent
    ownership handoff, or notable activity. */
export type LeadEvent = {
  id: number;
  event_category: "stage" | "assignment" | "ownership" | "activity";
  event_type: string;
  actor_type: "system" | "broker" | "hob" | "agent" | "ai";
  actor_id: number | null;
  actor_name: string | null;
  target_id: number | null;
  target_name: string | null;
  from_value: string | null;
  to_value: string | null;
  reason: string | null;
  meta: Record<string, unknown> | null;
  created_at: string | null;
};

export type LeadHistory = {
  events: LeadEvent[];
};

export type LeadQuery = {
  search?: string;
  signals?: string[];
  stage?: string;
  /** USPS state codes. Each location filter narrows the pool on its own. */
  states?: string[];
  /** Town keys from the location facets, e.g. "mattoon|IL". */
  cities?: string[];
  zips?: string[];
};
