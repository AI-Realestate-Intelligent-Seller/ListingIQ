/** A campaign is either still being written or already out the door. */
export type CampaignStatus = "draft" | "sent";

/** One campaign with the numbers the overview reports on. */
export type Campaign = {
  id: number;
  name: string;
  message_template: string;
  /** One reason for the whole batch. Blank falls back to each lead's own signal. */
  outreach_reason: string;
  status: CampaignStatus;
  sent_at: string | null;
  created_at: string | null;
  /** Unique property addresses held by the campaign. */
  recipients: number;
  /** Unique property addresses reached by an opening message. */
  delivered: number;
  /** Of those property addresses, the ones with an owner reply. */
  replied: number;
  /** Selected property addresses still without a reply. */
  no_reply: number;
  /** Selected property addresses the send could not reach. */
  not_sent: number;
  broker_id: number | null;
  broker_name: string | null;
  broker_email: string | null;
  broker_role: string | null;
};

/** One recipient of a draft, with the message they would actually receive. */
export type CampaignRecipient = {
  lead_id: number;
  owner_name: string | null;
  phone: string | null;
  property_address: string | null;
  area: string | null;
  /** Same shape the Lead Pool table renders. */
  signals: { key: string; label: string }[];
  score: number;
  stage: string | null;
  last_activity_at: string | null;
  text: string;
  /** Past two SMS segments, so the composer can warn about the cost. */
  is_long: boolean;
  /** Set once sent: these leads are no longer in the pool, so open them here. */
  conversation_id: number | null;
  replied: boolean;
  delivery_status: string | null;
  delivery_failure_reason: string | null;
};

export type CampaignSkip = {
  lead_id: number;
  owner_name: string | null;
  reason: string;
};

export type CampaignPreview = {
  campaign_id: number;
  recipients: CampaignRecipient[];
  skipped: CampaignSkip[];
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
  /** Set when the template will not render; Send stays disabled while it is. */
  template_error: string;
  /** Placeholder name → what it becomes, for the token help beside the editor. */
  tokens: Record<string, string>;
};

/** How much of a campaign carries one signal. */
export type CampaignSignalShare = {
  key: string;
  label: string;
  count: number;
  /** 0–1. A signal at or above 0.5 is what the set is about. */
  share: number;
};

export type CampaignReasonSuggestions = {
  signals: CampaignSignalShare[];
  suggestions: string[];
  /** "ai" when DeepSeek phrased them, "catalog" for the built-in wordings. */
  source: "ai" | "catalog";
  /** Why the catalog was used, when it was. Empty on the happy path. */
  note: string;
};

export type CampaignDetail = Campaign & {
  preview: CampaignPreview;
};

export type CampaignDraftResult = {
  campaign: CampaignDetail;
  /** Selected leads the draft could not take, each with the reason. */
  not_added: CampaignSkip[];
};

export type CampaignSendResult = {
  campaign_id: number;
  started: { lead_id: number; owner_name: string | null; conversation_id: number; text: string }[];
  skipped: CampaignSkip[];
};
