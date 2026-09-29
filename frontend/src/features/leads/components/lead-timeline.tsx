"use client";

import { useEffect, useRef, useState } from "react";

import { fetchLeadHistory } from "../api/leads-api";
import type { LeadEvent } from "../types/leads.types";

function formatMoment(value: string | null): string {
  if (!value) return "—";
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Icon + accent per category — ownership handoffs additionally split by
    who the thread just went to, since that's the toggle worth catching at a
    glance in a long AI<->agent history. */
function eventVisual(event: LeadEvent): { icon: string; accent: string } {
  if (event.event_category === "ownership") {
    return event.to_value === "bobbie"
      ? { icon: "✦", accent: "ai" }
      : { icon: "◆", accent: "agent" };
  }
  switch (event.event_category) {
    case "assignment":
      return { icon: "→", accent: "assignment" };
    case "activity":
      return { icon: "✓", accent: "activity" };
    default:
      return { icon: "●", accent: "stage" };
  }
}

const SOURCE_LABELS: Record<string, string> = {
  csv_import: "CSV import",
  batchdata: "BatchData",
  propertyradar: "PropertyRadar",
  dealmachine: "DealMachine",
};

function sourceLabel(source: string | null): string {
  if (!source) return "an unknown source";
  return SOURCE_LABELS[source] ?? source.replace(/_/g, " ");
}

function eventLabel(event: LeadEvent): string {
  switch (event.event_type) {
    case "intake":
      return `Entered the system via ${sourceLabel(event.to_value)}`;
    case "attached_to_campaign":
      return `Attached to campaign${event.to_value ? ` "${event.to_value}"` : ""}`;
    case "locked_to_campaign":
      return `Locked to campaign${event.to_value ? ` "${event.to_value}"` : ""}`;
    case "released_from_campaign":
      return `Released back to the pool${event.from_value ? ` (campaign "${event.from_value}" discarded)` : ""}`;
    case "assigned":
      return `Assigned to ${event.target_name ?? "an agent"}`;
    case "revoked":
      return "Assignment revoked";
    case "reassigned":
      return `Reassigned to ${event.target_name ?? "another agent"}`;
    case "stage_changed":
      return `Stage changed: ${event.from_value ?? "?"} → ${event.to_value ?? "?"}`;
    case "handover_to_ai":
      return "Handed back to AI (Bobbie)";
    case "handover_to_agent":
      return "Handed to the agent";
    case "meeting_booked":
      return "Meeting booked";
    case "outreach_sent":
      return `Outreach message sent${event.to_value ? ` (campaign "${event.to_value}")` : ""}`;
    case "first_reply_received":
      return "First reply received";
    default:
      return event.event_type.replace(/_/g, " ");
  }
}

function eventBy(event: LeadEvent): string | null {
  if (event.actor_type === "ai") return "Bobbie";
  if (event.actor_type === "system") return null;
  return event.actor_name ?? null;
}

type LeadTimelineProps = {
  /** null while nothing is selected, or when the selected thread has no
      lead-pool row behind it (started by hand, not from a campaign). */
  leadId: number | null;
  accessToken: string;
  /** Heading text; the Follow-ups panel wants something more specific than
      the generic "History" the drawer used. */
  title?: string;
  /** Bump this (e.g. after an action, or on a poll tick) to re-fetch even
      though leadId itself hasn't changed — a handover or stage change adds
      an event without swapping which lead is being shown. */
  refreshToken?: number;
};

/** A scrollable vertical timeline: one dot + line per event, oldest first at
    the top. Every AI<->agent ownership toggle gets its own entry rather than
    being collapsed, since that back-and-forth is exactly what a broker
    reviewing a lead wants to trace. */
export function LeadTimeline({ leadId, accessToken, title = "History", refreshToken = 0 }: LeadTimelineProps) {
  const [events, setEvents] = useState<LeadEvent[] | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const shownLeadId = useRef<number | null>(null);

  useEffect(() => {
    if (leadId === null) {
      shownLeadId.current = null;
      setEvents(null);
      setErrorMessage("");
      return;
    }
    const controller = new AbortController();
    // Switching leads clears the old list right away; a refreshToken-only
    // refetch (same lead) keeps it on screen so a poll doesn't flash
    // "Loading…" over a timeline that hasn't actually changed.
    if (shownLeadId.current !== leadId) {
      setEvents(null);
      setErrorMessage("");
    }
    fetchLeadHistory(leadId, accessToken, controller.signal)
      .then((history) => {
        shownLeadId.current = leadId;
        setEvents(history.events);
        // Polling is deliberately resilient: a backend reload or brief network
        // interruption may fail one refresh while the previous timeline stays
        // visible. Clear that transient error as soon as a later refresh
        // succeeds so a recovered server is not still reported as unreachable.
        setErrorMessage("");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setErrorMessage(error instanceof Error ? error.message : "Could not load the history.");
      });
    return () => controller.abort();
  }, [leadId, accessToken, refreshToken]);

  return (
    <section className="leads-drawer-section">
      <h4>{title}</h4>
      {leadId === null ? (
        <p className="leads-drawer-note">No lead record linked to this conversation.</p>
      ) : null}
      {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}
      {leadId !== null && events === null && !errorMessage ? <p className="sms-muted">Loading…</p> : null}
      {events && events.length === 0 ? (
        <p className="leads-drawer-note">No history yet.</p>
      ) : null}
      {events && events.length > 0 ? (
        <ol className="leads-timeline">
          {events.map((event) => {
            const { icon, accent } = eventVisual(event);
            const by = eventBy(event);
            return (
              <li key={event.id} className={`leads-timeline-item leads-timeline-${accent}`}>
                <span className="leads-timeline-dot" aria-hidden="true">{icon}</span>
                <div className="leads-timeline-content">
                  <p className="leads-timeline-label">{eventLabel(event)}</p>
                  <p className="leads-timeline-meta">
                    {formatMoment(event.created_at)}
                    {by ? ` · ${by}` : ""}
                  </p>
                  {event.reason ? <p className="leads-timeline-reason">{event.reason}</p> : null}
                </div>
              </li>
            );
          })}
        </ol>
      ) : null}
    </section>
  );
}
