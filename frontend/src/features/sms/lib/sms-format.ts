/** Presentation helpers shared by the SMS workspace and the Follow-ups board. */

import type { LeadStatus } from "../types/sms.types";

export const LEAD_LABELS: Record<LeadStatus, string> = {
  processing: "In progress",
  want_more_info: "Wants info",
  interested: "Interested",
  ready_to_sell: "Ready to sell",
  not_interested: "Not interested",
  no_response: "No response",
  dnc: "Do not contact",
};

/** The statuses a person may set by hand, in pipeline order. */
export const LEAD_STATUS_ORDER: LeadStatus[] = [
  "processing",
  "want_more_info",
  "interested",
  "ready_to_sell",
  "not_interested",
  "no_response",
  "dnc",
];

/** Two initials for the avatar, from the owner's name or their number. */
export function initials(party: { name?: string | null; contact: string }): string {
  const source = party.name?.trim() || party.contact.replace("+", "");
  const parts = source.split(/\s+/);
  return (parts.length > 1
    ? `${parts[0][0] ?? ""}${parts[1][0] ?? ""}`
    : source.slice(0, 2)
  ).toUpperCase();
}

/** Backend timestamps are naive UTC; mark them so they render in local time. */
export function toLocalDate(value: string | null): Date | null {
  if (!value) return null;
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  return Number.isNaN(stamp.getTime()) ? null : stamp;
}

export function formatTime(value: string | null): string {
  const stamp = toLocalDate(value);
  if (!stamp) return "";
  const today = new Date().toDateString() === stamp.toDateString();
  return today
    ? stamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })
    : stamp.toLocaleDateString([], { month: "short", day: "numeric" });
}

/** Clock time only, for the delivery line under a bubble. */
export function formatClock(value: string | null): string {
  const stamp = toLocalDate(value);
  return stamp ? stamp.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "";
}

/** Heading for the day divider that separates runs of messages. */
export function dateLabel(value: string | null): string {
  const stamp = toLocalDate(value);
  if (!stamp) return "";
  const day = stamp.toDateString();
  if (day === new Date().toDateString()) return "Today";
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  if (day === yesterday.toDateString()) return "Yesterday";
  return stamp.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" });
}
