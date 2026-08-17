import { deleteJson, getJson, patchJson, postJson } from "@/lib/api/http-client";

import type {
  Booking,
  CalendarAvailability,
  ConversationUpdate,
  NewConversationPayload,
  NewConversationResult,
  SmsConversation,
  SmsMessage,
} from "../types/sms.types";

const SMS = "/sms";

export function listConversations(
  accessToken: string,
  signal?: AbortSignal,
): Promise<SmsConversation[]> {
  return getJson<SmsConversation[]>(`${SMS}/conversations`, accessToken, signal);
}

export function createConversation(
  payload: NewConversationPayload,
  accessToken: string,
): Promise<NewConversationResult> {
  return postJson<NewConversationResult, NewConversationPayload>(
    `${SMS}/conversations`,
    payload,
    accessToken,
  );
}

export function listMessages(
  conversationId: number,
  accessToken: string,
  signal?: AbortSignal,
): Promise<SmsMessage[]> {
  return getJson<SmsMessage[]>(
    `${SMS}/conversations/${conversationId}/messages`,
    accessToken,
    signal,
  );
}

export function sendMessage(
  conversationId: number,
  text: string,
  accessToken: string,
): Promise<SmsMessage> {
  return postJson<SmsMessage, { text: string }>(
    `${SMS}/conversations/${conversationId}/messages`,
    { text },
    accessToken,
  );
}

/** Move a thread between Bobbie and the broker. */
export function setHandover(
  conversationId: number,
  to: "bobbie" | "broker",
  accessToken: string,
): Promise<SmsConversation> {
  return postJson<SmsConversation, Record<string, never>>(
    `${SMS}/conversations/${conversationId}/handover?to=${to}`,
    {} as Record<string, never>,
    accessToken,
  );
}

export function updateConversation(
  conversationId: number,
  changes: ConversationUpdate,
  accessToken: string,
): Promise<SmsConversation> {
  return patchJson<SmsConversation, ConversationUpdate>(
    `${SMS}/conversations/${conversationId}`,
    changes,
    accessToken,
  );
}

export function deleteConversation(
  conversationId: number,
  accessToken: string,
): Promise<{ ok: boolean }> {
  return deleteJson<{ ok: boolean }>(`${SMS}/conversations/${conversationId}`, accessToken);
}

export function getAvailability(accessToken: string): Promise<CalendarAvailability> {
  return getJson<CalendarAvailability>(`${SMS}/calendar/availability`, accessToken);
}

export function listBookings(accessToken: string): Promise<Booking[]> {
  return getJson<Booking[]>(`${SMS}/calendar/bookings`, accessToken);
}
