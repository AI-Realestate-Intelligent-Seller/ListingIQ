export type Config = {
  enabled: boolean;
  public_webhook_url: string;
  state: string;
  city: string;
  zip_codes: string[];
  county_fips: string;
  initial_rows: number;
  high_equity_min: number;
  selected_categories: string[];
  monitor_new_matches: boolean;
  monitor_status_changes: boolean;
  skiptrace_new: boolean;
  skiptrace_initial: boolean;
  contact_mode: "primary" | "all";
  phone_limit: number;
  email_limit: number;
  export_limit: number;
  monitored_limit: number;
  billing_cycle_start: string | null;
};
export type Summary = {
  status: string;
  enabled: boolean;
  config: Config;
  token_configured: boolean;
  connection_status: string;
  last_successful_connection_at: string | null;
  last_successful_sync_at: string | null;
  last_webhook_at: string | null;
  last_error: string | null;
  busy: boolean;
  local_mode: boolean;
  webhook_url: string;
  webhook_id: string | null;
  webhook_secret_configured: boolean;
  webhook_registration_allowed: boolean;
  webhook_message: string | null;
  categories: { key: string; label: string }[];
  metrics: Record<string, number | null>;
  lead_activity?: {
    new_leads_7d: number;
    new_leads_30d: number;
    lead_updates_7d: number;
    lead_updates_30d: number;
  };
  jobs_by_category?: {
    category: string;
    list_name: string;
    total_count: number | null;
    is_monitored: boolean;
    automation_status: string;
    last_synced_at: string | null;
  }[];
  usage: {
    cycle_start: string | null;
    cycle_end: string | null;
    used: Record<string, number>;
    remaining: Record<string, number>;
    provider_reported_cost: string;
    no_overage: boolean;
  };
  max_initial_rows: number;
  per_list_limit: number;
};
export type ImportPreview = {
  preview_id: string;
  selected_categories: string[];
  rows_per_category: number;
  raw_radar_ids: Record<string, string[]>;
  total_before_deduplication: number;
  unique_count: number;
  duplicate_occurrences: number;
  category_overlap: Record<string, string[]>;
  estimated_exports: number;
  existing_count: number;
  previews: {
    resultCount: number;
    totalCost: string;
    quantityFreeRemaining: number;
  }[];
  safe: boolean;
  reason: string | null;
};
export type MonitoringPreview = {
  preview_id: string;
  safe: boolean;
  reason: string | null;
  union_count: number;
  counts: Record<string, number>;
};
export type Row = Record<string, unknown> & { id: number };
export type Records = {
  items: Row[];
  total: number;
  offset: number;
  limit: number;
};
