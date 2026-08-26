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
  /** Leads held by the campaign, whether or not they could be texted. */
  recipients: number;
  /** Threads the opening message actually went out on. */
  delivered: number;
  /** Of those, the ones the owner has answered. */
  replied: number;
  /** Delivered but still silent. */
  no_reply: number;
  /** Members the send could not reach — no phone, on the DNC list, already texted. */
  not_sent: number;
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
