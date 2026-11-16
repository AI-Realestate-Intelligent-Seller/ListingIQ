import type { UserRole } from "@/features/auth/types/auth.types";

import { actionFromUrl, parseNotificationAction, type DashboardTab, type NotificationAction } from "./notification-action";

/**
 * URL scheme for the dashboard. One section per path segment, mirroring the
 * API resources behind each screen, with the record a navigation focuses on as
 * the next segment:
 *
 *   /dashboard/{role}                         → last section this tab used (or Overview)
 *   /dashboard/{role}/{section}               → overview | leads | campaigns | assignments
 *                                               | followups | directory | calendar | persona
 *   /dashboard/{role}/followups?conversation_id={id}[&campaign_id={id}]
 *                                               → the open chat, shareable
 *   /dashboard/{role}/followups/{conversationId} (older form, still read)
 *   /dashboard/{role}/leads/{leadId}
 *   /dashboard/{role}/assignments/{leadId}
 *   /dashboard/{role}/campaigns/{campaignId}  → the draft to compose
 *   /dashboard/{role}/calendar/{bookingId}
 *
 * Legacy `/dashboard?view=…&conversation_id=…` links (push notifications,
 * service worker, backend action URLs) still resolve through `actionFromUrl`.
 */

/** Which id the second path segment carries for each section. */
const PATH_ID: Partial<Record<DashboardTab, keyof NotificationAction>> = {
  calendar: "booking_id",
  leads: "lead_id",
  assignments: "lead_id",
  campaigns: "campaign_id",
};

/** Role rules that were always applied before a section opens. */
export function allowedTarget(view: DashboardTab, role: UserRole | undefined): DashboardTab {
  let target = view;
  if (target === "assignments" && role === "agent") target = "leads";
  if ((target === "directory" && role !== "hob") ||
      (target === "assignments" && role !== "broker") ||
      (target === "campaigns" && role === "agent")) target = "overview";
  return target;
}

/** The destination after role rules, keeping only the ids that section uses. */
export function resolveDestination(action: NotificationAction, role: UserRole | undefined): NotificationAction {
  const target = allowedTarget(action.view, role);
  const destination: NotificationAction = { view: target };
  if (target === action.view) {
    const keys = target === "followups" ? ["conversation_id", "campaign_id"] as const
      : target === "calendar" ? ["booking_id"] as const
      : target === "leads" || target === "assignments" ? ["lead_id"] as const
      : target === "campaigns" ? ["campaign_id"] as const : [];
    for (const key of keys) {
      const id = action[key];
      if (id !== undefined) destination[key] = id;
    }
  }
  return destination;
}

export function dashboardPath(role: string, action: NotificationAction): string {
  const segments = ["", "dashboard", role, action.view];
  const idKey = PATH_ID[action.view];
  const id = idKey ? action[idKey] : undefined;
  if (id !== undefined) segments.push(String(id));
  const query = new URLSearchParams();
  // Follow-ups keeps the open chat (and an optional campaign filter) in the
  // query string, so a copied URL opens the same conversation.
  if (action.view === "followups") {
    if (action.conversation_id !== undefined) query.set("conversation_id", String(action.conversation_id));
    if (action.campaign_id !== undefined) query.set("campaign_id", String(action.campaign_id));
  }
  const search = query.toString();
  return segments.join("/") + (search ? `?${search}` : "");
}

/** Parse `/dashboard/{role}/{section}[/{id}]`. Null when no section is in the path. */
export function actionFromDashboardPath(pathname: string, search = ""): NotificationAction | null {
  const match = pathname.match(/^\/dashboard\/[^/]+\/([^/]+)(?:\/(\d+))?\/?$/);
  if (!match) return null;
  const view = match[1];
  const raw: Record<string, unknown> = { view };
  const idKey = PATH_ID[view as DashboardTab];
  if (idKey && match[2]) raw[idKey] = match[2];
  const query = new URLSearchParams(search);
  if (view === "followups") {
    raw.conversation_id = query.get("conversation_id") ?? match[2];
    if (query.get("campaign_id")) raw.campaign_id = query.get("campaign_id");
  }
  return parseNotificationAction(raw);
}

/** Legacy query-string link (`?view=…`) on any dashboard URL. */
export function legacyActionFromSearch(pathname: string, search: string): NotificationAction | null {
  if (!new URLSearchParams(search).get("view")) return null;
  return actionFromUrl(pathname + search);
}

/** Same URL, but under the signed-in user's own role. */
export function pathForRole(pathname: string, role: string): string {
  if (/^\/dashboard\/?$/.test(pathname)) return `/dashboard/${role}`;
  return pathname.replace(/^\/dashboard\/[^/]+/, `/dashboard/${role}`);
}
