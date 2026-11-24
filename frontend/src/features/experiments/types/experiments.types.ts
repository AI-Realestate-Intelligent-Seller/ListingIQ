export type ExperimentVariantInput = {
  label: string;
  message_template: string;
  lead_ids: number[];
  followup_enabled: boolean;
  followup_template: string | null;
  followup_delay_hours: number | null;
};

export type ExperimentDimension = "tone" | "length" | "reason" | "open_question" | "other";

export type ExperimentCreateInput = {
  name: string;
  goal_notes: string;
  /** Comma-separated selected dimensions; the API stores the experiment definition verbatim. */
  test_dimension: string;
  test_dimension_other: string;
  winner_metric: "reply_rate" | "positive_response_rate";
  allocation_mode: "balanced" | "random";
  timezone: string;
  scheduled_at: string | null;
  launch_now?: boolean;
  variants: ExperimentVariantInput[];
};

export type ExperimentVariant = Omit<ExperimentVariantInput, "lead_ids"> & {
  campaign_id: number;
  lead_count: number;
  color_index: number;
  followup_editable?: boolean;
  followup_scheduled_at?: string | null;
};

export type ExperimentFollowupUpdate = Pick<ExperimentVariantInput,
  "followup_enabled" | "followup_template" | "followup_delay_hours"
>;

export type Experiment = Omit<ExperimentCreateInput, "variants" | "launch_now" | "scheduled_at"> & {
  id: number;
  scheduled_at: string;
  status: "scheduled" | "launching" | "running" | "cancelled" | "failed";
  variants: ExperimentVariant[];
  campaign_ids: number[];
  total_leads: number;
  outreach_conversations: number;
  /** True once every variant campaign has finished sending its queued outreach. */
  outreach_complete: boolean;
  expected_max_cost: number;
  error: string;
  launched_at: string | null;
  cancelled_at: string | null;
  created_at: string;
};
