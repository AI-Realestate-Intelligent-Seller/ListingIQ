import type { Booking, LeadStatus, QueueStatus } from "@/features/sms/types/sms.types";

/** The decision the assignee has recorded on a replied lead. */
export type FollowUpState = "pending" | "accepted" | "declined";

/** Why a thread is on the follow-up list, in the order the API triages them. */
export type FollowUpReason =
  | "opted_out"
  | "reply_needed"
  | "appointment_booked"
  | "appointment_pending"
  | "not_interested"
  | "no_response"
  | "in_conversation";

export type FollowUp = {
  id: number;
  /** The lead pool row behind the thread, for the property details panel. */
  lead_id: number | null;
  /** The campaign that opened this thread, when one did. */
  campaign_id: number | null;
  campaign_name: string | null;
  contact: string;
  name: string | null;
  property_address: string | null;
  /** From the lead pool row behind the thread, when it came from an import. */
  area: string | null;
  properties: {
    lead_id: number;
    address: string | null;
    area: string | null;
    campaign_id: number | null;
    campaign_name: string | null;
    signals: string[];
  }[];
  has_multiple_properties: boolean;
  ai_enabled: boolean;
  handled_by: "bobbie" | "broker";
  awaiting_broker_reply: boolean;
  lead_status: LeadStatus;
  queue_status: QueueStatus;
  dnc_alert: boolean;
  meeting_booked: boolean;
  followup_state: FollowUpState;
  reason: FollowUpReason;
  reason_label: string;
  waiting_days: number | null;
  reply_count: number;
  message_count: number;
  first_reply_at: string | null;
  last_reply_at: string | null;
  latest_message: string | null;
  latest_message_at: string | null;
  created_at: string | null;
};

export type AppointmentPayload = {
  start_at: string;
  end_at: string;
  title?: string;
  /** Text the owner the confirmation and the meeting link. */
  notify: boolean;
};

export type AppointmentResult = {
  booking: Booking;
  notified: boolean;
  note: string | null;
  followup: FollowUp;
};
