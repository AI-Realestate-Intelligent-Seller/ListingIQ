export type DashboardTab = "overview" | "directory" | "assignments" | "leads" | "campaigns" | "followups" | "calendar";

export type NotificationAction = {
  view: DashboardTab;
  conversation_id?: number;
  booking_id?: number;
  lead_id?: number;
  campaign_id?: number;
};

const tabs: DashboardTab[] = ["overview", "directory", "assignments", "leads", "campaigns", "followups", "calendar"];

export function parseNotificationAction(value: unknown): NotificationAction | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  if (!tabs.includes(raw.view as DashboardTab)) return null;
  const action: NotificationAction = { view: raw.view as DashboardTab };
  for (const key of ["conversation_id", "booking_id", "lead_id", "campaign_id"] as const) {
    const id = Number(raw[key]);
    if (Number.isSafeInteger(id) && id > 0) action[key] = id;
  }
  return action;
}

export function actionFromUrl(value?: string | null): NotificationAction | null {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return null;
  const url = new URL(value, "https://dashboard.local");
  const legacy: Record<string, DashboardTab> = {
    "/assignments": "leads", "/agent": "calendar", "/calendar": "calendar", "/followups": "followups",
  };
  const booking = url.pathname.match(/^\/calendar\/bookings\/(\d+)$/);
  return parseNotificationAction({
    ...Object.fromEntries(url.searchParams),
    view: url.searchParams.get("view") || (booking ? "calendar" : legacy[url.pathname]),
    ...(booking ? { booking_id: booking[1] } : {}),
  });
}

export function resolveNotificationAction(notification: {
  action?: NotificationAction | null;
  type: string;
  conversationId?: number | null;
  bookingId?: number | null;
  actionUrl?: string | null;
}): NotificationAction {
  const action = parseNotificationAction(notification.action);
  if (action) return action;
  if (notification.conversationId) return { view: "followups", conversation_id: notification.conversationId };
  if (notification.bookingId) return { view: "calendar", booking_id: notification.bookingId };
  return actionFromUrl(notification.actionUrl) ?? {
    view: ({ CUSTOMER_REPLY: "followups", LEAD_ASSIGNED: "leads", booking_created: "calendar", BOOKING_REMINDER: "calendar" } as Record<string, DashboardTab>)[notification.type] ?? "overview",
  };
}
