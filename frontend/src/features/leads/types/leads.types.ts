/** A signal is why a property is worth a conversation (FSBO, probate, …). */
export type LeadSignal = {
  key: string;
  label: string;
};

/** Stages are derived by the backend; the broker never sets one by hand. */
export type LeadStage = "dnc" | "in_campaign" | "needs_review" | "new" | "ready";

export type Lead = {
  id: number;
  owner_name: string | null;
  phone: string | null;
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

export type LeadFacets = {
  total: number;
  signals: Record<string, number>;
  stages: Record<string, number>;
};

export type LeadPoolResponse = {
  leads: Lead[];
  facets: LeadFacets;
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
  /** Rows that could be read as a lead. Nothing is merged, so this is a count of rows. */
  importable: number;
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

export type CampaignResult = {
  started: { lead_id: number; owner_name: string | null; conversation_id: number; text: string }[];
  skipped: { lead_id: number; owner_name: string | null; reason: string }[];
};

export type LeadQuery = {
  search?: string;
  signals?: string[];
  stage?: string;
};
