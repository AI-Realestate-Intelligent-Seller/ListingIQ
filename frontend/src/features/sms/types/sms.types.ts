export type LeadStatus =
  | "processing"
  | "want_more_info"
  | "interested"
  | "ready_to_sell"
  | "not_interested"
  | "no_response"
  | "dnc";

export type QueueStatus = "idle" | "waiting" | "active" | "completed" | "failed" | "dead";

export type SmsConversation = {
  id: number;
  /** The lead pool row behind the thread, when it came from an import. Null for
      a thread started by hand, which has no property record to show. */
  lead_id: number | null;
  contact: string;
  name: string | null;
  property_address: string | null;
  ai_enabled: boolean;
  /** Who owns the next reply. A broker-owned thread never wakes Bobbie. */
  handled_by: "bobbie" | "broker";
  /** The owner replied while the thread was broker-owned. */
  awaiting_broker_reply: boolean;
  lead_status: LeadStatus;
  queue_status: QueueStatus;
  dnc_alert: boolean;
  meeting_booked: boolean;
  created_at: string | null;
  latest_message: string | null;
  latest_message_at: string | null;
  message_count: number;
};

export type SmsMessage = {
  id: number;
  direction: "inbound" | "outbound";
  from_number: string | null;
  to_number: string | null;
  text: string | null;
  status: string | null;
  event_type: string | null;
  created_at: string | null;
};

export type NewConversationPayload = {
  contact: string;
  name: string;
  property_address: string;
  outreach_reason: string;
  ai_enabled: boolean;
};

export type NewConversationResult = {
  conversation: SmsConversation;
  started: boolean;
  text: string | null;
};

export type ConversationUpdate = Partial<
  Pick<SmsConversation, "name" | "property_address" | "ai_enabled">
>;

export type CalendarSlot = {
  start_at: string;
  end_at: string;
  label: string;
};

export type CalendarAvailability = {
  timezone: string;
  current_time: string;
  slot_minutes: number;
  slots: CalendarSlot[];
};

export type Booking = {
  id: number;
  phone: string;
  name: string | null;
  title: string;
  start_at: string;
  end_at: string;
  join_url: string;
};
