import { getJson, postJson } from "@/lib/api/http-client";

import type {
  LoftyExportStatus,
  LoftyPreview,
  LoftySendPayload,
} from "../types/followups.types";

const FOLLOWUPS = "/followups";

/** What Send to Lofty would transfer. Changes nothing in Lofty. */
export function getLoftyPreview(conversationId: number, accessToken: string): Promise<LoftyPreview> {
  return getJson<LoftyPreview>(`${FOLLOWUPS}/${conversationId}/lofty`, accessToken);
}

/** Drafts the handoff summary with the configured AI provider. Nothing is sent. */
export function generateLoftySummary(
  conversationId: number,
  accessToken: string,
): Promise<{ summary: string }> {
  return postJson<{ summary: string }, Record<string, never>>(
    `${FOLLOWUPS}/${conversationId}/lofty/summary`,
    {},
    accessToken,
  );
}

/** Creates/updates the Lofty lead, mutes Anna and saves the notes. Safe to retry. */
export function sendToLofty(
  conversationId: number,
  payload: LoftySendPayload,
  accessToken: string,
): Promise<LoftyExportStatus> {
  return postJson<LoftyExportStatus, LoftySendPayload>(
    `${FOLLOWUPS}/${conversationId}/lofty`,
    payload,
    accessToken,
  );
}
