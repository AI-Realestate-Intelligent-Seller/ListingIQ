import { getJson, patchJson, postJson } from "@/lib/api/http-client";
import type { LeadStatus } from "@/features/sms/types/sms.types";

import type {
  AppointmentPayload,
  AppointmentResult,
  FollowUp,
  FollowUpState,
} from "../types/followups.types";

const FOLLOWUPS = "/followups";

/**
 * Reading a thread and moving it between Bobbie and the assignee are the SMS
 * workspace's own endpoints; Follow-ups reuses them rather than adding a second
 * route for each. See `listMessages` and `setHandover` in features/sms/api.
 */
export function listFollowUps(
  accessToken: string,
  scope: "replied" | "all" = "replied",
  state?: FollowUpState,
  signal?: AbortSignal,
  campaignId?: number,
): Promise<FollowUp[]> {
  const query = new URLSearchParams({ scope });
  if (state) query.set("state", state);
  if (campaignId !== undefined) query.set("campaign_id", String(campaignId));
  return getJson<FollowUp[]>(`${FOLLOWUPS}?${query}`, accessToken, signal);
}

export function setFollowUpState(
  conversationId: number,
  state: FollowUpState,
  accessToken: string,
): Promise<FollowUp> {
  return postJson<FollowUp, { state: FollowUpState }>(
    `${FOLLOWUPS}/${conversationId}/state`,
    { state },
    accessToken,
  );
}

export function setFollowUpStatus(
  conversationId: number,
  leadStatus: LeadStatus,
  accessToken: string,
): Promise<FollowUp> {
  return patchJson<FollowUp, { lead_status: LeadStatus }>(
    `${FOLLOWUPS}/${conversationId}`,
    { lead_status: leadStatus },
    accessToken,
  );
}

export function bookAppointment(
  conversationId: number,
  payload: AppointmentPayload,
  accessToken: string,
): Promise<AppointmentResult> {
  return postJson<AppointmentResult, AppointmentPayload>(
    `${FOLLOWUPS}/${conversationId}/appointment`,
    payload,
    accessToken,
  );
}
