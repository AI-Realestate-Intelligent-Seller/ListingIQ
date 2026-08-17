"use client";

import { useEffect, useState } from "react";

import { fetchLead } from "../api/leads-api";
import type { LeadDetail, LeadStage } from "../types/leads.types";

const STAGE_CLASS: Record<LeadStage, string> = {
  ready: "ready",
  new: "new",
  in_campaign: "campaign",
  needs_review: "review",
  dnc: "dnc",
};

const LEAD_STATUS_LABELS: Record<string, string> = {
  processing: "In progress",
  want_more_info: "Wants info",
  interested: "Interested",
  ready_to_sell: "Ready to sell",
  not_interested: "Not interested",
  no_response: "No response",
  dnc: "Do not contact",
};

/** "listing_price" -> "Listing price"; the vendor's own keys stay recognisable. */
function attributeLabel(key: string): string {
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Trailing ".0" on every number is spreadsheet noise, not data. */
function attributeValue(value: string): string {
  return /^-?\d+\.0$/.test(value) ? value.slice(0, -2) : value;
}

function formatMoment(value: string | null): string {
  if (!value) return "—";
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

type LeadDetailDrawerProps = {
  /** The lead to show, or null when the drawer is closed. */
  leadId: number | null;
  accessToken: string;
  stageLabels: Record<string, string>;
  onClose: () => void;
  onOpenConversation: (conversationId: number) => void;
  onStartCampaign: (leadId: number) => void;
  onRemove: (lead: LeadDetail) => void;
};

/**
 * A right-hand panel with everything known about one property and its owner.
 * Fixed-position and full-height, so it behaves the same on a laptop and a
 * phone — only its width changes.
 */
export function LeadDetailDrawer({
  leadId,
  accessToken,
  stageLabels,
  onClose,
  onOpenConversation,
  onStartCampaign,
  onRemove,
}: LeadDetailDrawerProps) {
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  useEffect(() => {
    if (leadId === null) return;
    const controller = new AbortController();
    setIsLoading(true);
    setErrorMessage("");
    fetchLead(leadId, accessToken, controller.signal)
      .then((detail) => setLead(detail))
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setErrorMessage(error instanceof Error ? error.message : "Could not load the lead.");
      })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, [leadId, accessToken]);

  // Escape closes it, as with the other overlays.
  useEffect(() => {
    if (leadId === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [leadId, onClose]);

  const isOpen = leadId !== null;
  // Keep the previous lead mounted while a new one loads so the panel never blinks.
  const shown = lead && lead.id === leadId ? lead : null;

  return (
    <>
      <div
        className={`leads-drawer-scrim${isOpen ? " open" : ""}`}
        role="presentation"
        onClick={onClose}
      />
      <aside
        className={`leads-drawer${isOpen ? " open" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label="Lead details"
        aria-hidden={!isOpen}
      >
        <header className="leads-drawer-header">
          <div>
            <span className="sms-eyebrow">PROPERTY DETAILS</span>
            <h3>{shown?.owner_name || shown?.phone || "Lead"}</h3>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close details">
            ✕
          </button>
        </header>

        <div className="leads-drawer-body">
          {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}
          {isLoading && !shown ? <p className="sms-muted">Loading…</p> : null}

          {shown ? (
            <>
              <section className="leads-drawer-section">
                <h4>Property</h4>
                <dl className="leads-facts">
                  <div>
                    <dt>Address</dt>
                    <dd>{shown.property_address || "Not on file"}</dd>
                  </div>
                  <div>
                    <dt>Area</dt>
                    <dd>{shown.area || "Not on file"}</dd>
                  </div>
                </dl>
              </section>

              <section className="leads-drawer-section">
                <h4>Owner</h4>
                <dl className="leads-facts">
                  <div>
                    <dt>Name</dt>
                    <dd>{shown.owner_name || "Not on file"}</dd>
                  </div>
                  <div>
                    <dt>Phone</dt>
                    <dd>
                      {shown.phone ? (
                        <a href={`tel:${shown.phone}`}>{shown.phone}</a>
                      ) : (
                        "Not on file"
                      )}
                    </dd>
                  </div>
                </dl>
              </section>

              {Object.keys(shown.details).length > 0 ? (
                <section className="leads-drawer-section">
                  <h4>Property details</h4>
                  <dl className="leads-facts">
                    {Object.entries(shown.details).map(([key, value]) => (
                      <div key={key}>
                        <dt>{attributeLabel(key)}</dt>
                        <dd>{attributeValue(value)}</dd>
                      </div>
                    ))}
                  </dl>
                  <p className="leads-drawer-note">
                    From the import. Bobbie may cite these facts, and nothing beyond them.
                  </p>
                </section>
              ) : null}

              <section className="leads-drawer-section">
                <h4>Signals</h4>
                {shown.signals.length === 0 ? (
                  <p className="leads-drawer-note">No signals on this record.</p>
                ) : (
                  <div className="leads-signals">
                    {shown.signals.map((signal) => (
                      <span key={signal.key} className="leads-signal">{signal.label}</span>
                    ))}
                  </div>
                )}
              </section>

              {shown.outreach_reason ? (
                <section className="leads-drawer-section">
                  <h4>Outreach reason</h4>
                  <p className="leads-reason">{shown.outreach_reason}</p>
                  <p className="leads-drawer-note">
                    Came with the lead. Bobbie opens with this rather than a generic line.
                  </p>
                </section>
              ) : null}

              <section className="leads-drawer-section">
                <h4>
                  Score
                  <span className={`leads-score ${shown.score >= 80 ? "strong" : shown.score >= 70 ? "fair" : "plain"}`}>
                    {shown.score}
                  </span>
                </h4>
                <ul className="leads-score-breakdown">
                  {shown.score_breakdown.map((part) => (
                    <li key={part.label}>
                      <span>{part.label}</span>
                      <span>+{part.points}</span>
                    </li>
                  ))}
                </ul>
                <p className="leads-drawer-note">
                  A transparent sum of signals and contactability — it orders your review
                  queue, it does not predict a sale.
                </p>
              </section>

              <section className="leads-drawer-section">
                <h4>Status</h4>
                <dl className="leads-facts">
                  <div>
                    <dt>Stage</dt>
                    <dd>
                      <span className={`leads-stage ${STAGE_CLASS[shown.stage]}`}>
                        {stageLabels[shown.stage] ?? shown.stage}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Last activity</dt>
                    <dd>{shown.last_activity_at ? formatMoment(shown.last_activity_at) : "Never contacted"}</dd>
                  </div>
                  <div>
                    <dt>Added</dt>
                    <dd>{formatMoment(shown.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Last refreshed</dt>
                    <dd>{formatMoment(shown.refreshed_at)}</dd>
                  </div>
                </dl>
              </section>

              {shown.conversation ? (
                <section className="leads-drawer-section">
                  <h4>Conversation</h4>
                  <dl className="leads-facts">
                    <div>
                      <dt>Handled by</dt>
                      <dd>{shown.conversation.handled_by === "bobbie" ? "✦ Bobbie" : "You"}</dd>
                    </div>
                    <div>
                      <dt>Lead status</dt>
                      <dd>
                        {LEAD_STATUS_LABELS[shown.conversation.lead_status] ??
                          shown.conversation.lead_status}
                      </dd>
                    </div>
                    <div>
                      <dt>Messages</dt>
                      <dd>{shown.conversation.message_count}</dd>
                    </div>
                    {shown.conversation.meeting_booked ? (
                      <div>
                        <dt>Meeting</dt>
                        <dd>Booked</dd>
                      </div>
                    ) : null}
                  </dl>
                  {shown.conversation.latest_message ? (
                    <blockquote className="leads-last-message">
                      {shown.conversation.latest_message}
                      <cite>{formatMoment(shown.conversation.latest_message_at)}</cite>
                    </blockquote>
                  ) : null}
                </section>
              ) : null}
            </>
          ) : null}
        </div>

        {shown ? (
          <footer className="leads-drawer-actions">
            {shown.conversation ? (
              <button
                className="button"
                type="button"
                onClick={() => onOpenConversation(shown.conversation!.id)}
              >
                Open conversation
              </button>
            ) : (
              <button
                className="button"
                type="button"
                onClick={() => onStartCampaign(shown.id)}
                disabled={shown.stage === "dnc" || !shown.phone}
                title={
                  shown.stage === "dnc"
                    ? "This owner is on the do-not-contact list."
                    : !shown.phone
                      ? "No phone number to text."
                      : undefined
                }
              >
                ✦ Start with Bobbie
              </button>
            )}
            <button type="button" className="sms-button-secondary" onClick={() => onRemove(shown)}>
              Remove
            </button>
          </footer>
        ) : null}
      </aside>
    </>
  );
}
