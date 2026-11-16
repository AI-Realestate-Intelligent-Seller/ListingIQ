"use client";

import { useEffect, useRef, useState } from "react";

import { generateLoftySummary, getLoftyPreview, sendToLofty } from "../api/lofty-api";
import type { LoftyExportStatus, LoftyPreview } from "../types/followups.types";

type LoftyExportDialogProps = {
  /** The conversation to send; null keeps the dialog closed. */
  conversationId: number | null;
  accessToken: string;
  onClose: () => void;
};

/**
 * Summaries already generated (or edited) in this browser session, so closing
 * and reopening the dialog does not spend another AI request. Keyed by
 * conversation and invalidated when the thread gains messages.
 */
const summaryCache = new Map<number, { summary: string; messageCount: number }>();

const stampFormat = new Intl.DateTimeFormat(undefined, {
  month: "short",
  day: "numeric",
  year: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

function stamp(value: string | null): string {
  if (!value) return "Time unknown";
  // The API sends naive UTC timestamps.
  const date = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? value : stampFormat.format(date);
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

/**
 * Send to Lofty: review what will be transferred, edit the AI summary, then
 * confirm. Nothing reaches Lofty until the agent presses Send.
 */
export function LoftyExportDialog({ conversationId, accessToken, onClose }: LoftyExportDialogProps) {
  const [preview, setPreview] = useState<LoftyPreview | null>(null);
  // The board mounts a fresh dialog per conversation (keyed), so state starts clean.
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [summary, setSummary] = useState("");
  const [isSummarizing, setIsSummarizing] = useState(false);
  const [summaryError, setSummaryError] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [result, setResult] = useState<LoftyExportStatus | null>(null);
  const [chosenLeadId, setChosenLeadId] = useState("");
  // A ref, not state: a double click lands before React re-renders.
  const sendingRef = useRef(false);

  const isOpen = conversationId !== null;

  async function summarize(id: number, messageCount: number): Promise<void> {
    setIsSummarizing(true);
    setSummaryError("");
    try {
      const response = await generateLoftySummary(id, accessToken);
      setSummary(response.summary);
      summaryCache.set(id, { summary: response.summary, messageCount });
    } catch (error) {
      setSummaryError(errorText(error, "The AI summary could not be generated."));
    } finally {
      setIsSummarizing(false);
    }
  }

  useEffect(() => {
    if (conversationId === null) return;
    let cancelled = false;
    getLoftyPreview(conversationId, accessToken)
      .then((data) => {
        if (cancelled) return;
        setPreview(data);
        const cached = summaryCache.get(conversationId);
        if (cached && cached.messageCount === data.messages.length) {
          setSummary(cached.summary);
        } else if (data.export?.summary) {
          setSummary(data.export.summary);
        } else if (data.available && data.messages.length > 0) {
          void summarize(conversationId, data.messages.length);
        }
      })
      .catch((error: unknown) => {
        if (!cancelled) setLoadError(errorText(error, "Could not load the Lofty preview."));
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // summarize is stable for a given token; re-running on it would refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationId, accessToken]);

  if (!isOpen) return null;

  const exportStatus = result ?? preview?.export ?? null;
  const isComplete = exportStatus?.status === "completed";
  const needsReview = exportStatus?.status === "needs_review";
  // Once notes have started, the summary already frozen into them is used.
  const summaryLocked = exportStatus?.status === "failed" && exportStatus.step === "notes";
  const hasNewMessages =
    isComplete &&
    preview !== null &&
    preview.messages.length > 0 &&
    preview.messages[preview.messages.length - 1].id !== exportStatus?.last_message_id;

  function close(): void {
    if (sendingRef.current) return;
    onClose();
  }

  async function submit(resync = false): Promise<void> {
    if (sendingRef.current || conversationId === null) return;
    if (!summary.trim() && !summaryLocked) {
      setSendError("Add a summary before sending to Lofty.");
      return;
    }
    if (needsReview && !chosenLeadId) {
      setSendError("Choose the matching Lofty lead first.");
      return;
    }
    sendingRef.current = true;
    setIsSending(true);
    setSendError("");
    try {
      const status = await sendToLofty(
        conversationId,
        {
          summary: summary.trim(),
          ...(resync ? { resync: true } : {}),
          ...(needsReview && chosenLeadId ? { lofty_lead_id: chosenLeadId } : {}),
        },
        accessToken,
      );
      setResult(status);
      if (status.status === "completed" && preview) {
        setPreview({ ...preview, export: status });
      }
    } catch (error) {
      setSendError(errorText(error, "Could not send to Lofty."));
    } finally {
      sendingRef.current = false;
      setIsSending(false);
    }
  }

  const primaryLabel = isSending
    ? "Sending…"
    : needsReview
      ? "Send with selected lead"
      : exportStatus?.status === "failed"
        ? "Retry send"
        : "Send to Lofty";

  return (
    <div className="sms-dialog-scrim" role="presentation" onClick={close}>
      <section
        className="sms-dialog lofty-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lofty-dialog-title"
        aria-busy={isSending}
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">LOFTY CRM</span>
            <h2 id="lofty-dialog-title">Send to Lofty</h2>
          </div>
          <button
            type="button"
            className="sms-icon-button"
            onClick={close}
            aria-label="Close"
            disabled={isSending}
          >
            ✕
          </button>
        </header>

        {isLoading ? <p className="sms-muted">Loading what will be sent…</p> : null}
        {loadError ? <p className="sms-error" role="alert">{loadError}</p> : null}

        {preview ? (
          <div className="lofty-body">
            {!preview.available ? (
              <p className="sms-error" role="alert">{preview.unavailable_reason}</p>
            ) : null}

            {isComplete && exportStatus ? (
              <div className="lofty-result success" role="status">
                <strong>Sent to Lofty successfully</strong>
                <ul>
                  <li>✓ Lead {exportStatus.lead_action === "existing" ? "updated" : "created"}</li>
                  <li>✓ Anna muted</li>
                  <li>
                    ✓ Conversation notes saved
                    {exportStatus.notes_total > 1 ? ` (${exportStatus.notes_total} notes)` : ""}
                  </li>
                </ul>
                {exportStatus.lofty_url ? (
                  <a href={exportStatus.lofty_url} target="_blank" rel="noopener noreferrer">
                    Open in Lofty ↗
                  </a>
                ) : null}
                <small>
                  Anna is muted for this lead. Other Lofty automations or enrollment rules may still
                  apply.
                </small>
              </div>
            ) : null}

            {exportStatus && !isComplete && exportStatus.status !== "pending" ? (
              <div className={`lofty-result ${needsReview ? "warning" : "error"}`} role="alert">
                <strong>
                  {needsReview
                    ? "Choose the matching Lofty lead"
                    : exportStatus.status === "in_progress"
                      ? "A send is already in progress"
                      : "Not fully sent to Lofty"}
                </strong>
                <ul>
                  <li>{exportStatus.lofty_lead_id ? "✓" : "•"} Lead {exportStatus.lofty_lead_id
                    ? `${exportStatus.lead_action === "existing" ? "updated" : "created"} (#${exportStatus.lofty_lead_id})`
                    : "not saved yet"}</li>
                  <li>{exportStatus.mute_verified ? "✓" : "•"} Anna {exportStatus.mute_verified ? "muted" : "not muted yet"}</li>
                  <li>
                    {exportStatus.notes_total > 0 && exportStatus.notes_saved === exportStatus.notes_total ? "✓" : "•"}{" "}
                    Notes saved {exportStatus.notes_saved}/{exportStatus.notes_total || "–"}
                  </li>
                </ul>
                {exportStatus.last_error ? <p>{exportStatus.last_error}</p> : null}
                {needsReview && exportStatus.candidate_lead_ids.length > 0 ? (
                  <fieldset className="lofty-candidates">
                    <legend>Matching Lofty leads</legend>
                    {exportStatus.candidate_lead_ids.map((leadId) => (
                      <label key={leadId}>
                        <input
                          type="radio"
                          name="lofty-lead"
                          value={leadId}
                          aria-label={`Lead #${leadId}`}
                          checked={chosenLeadId === leadId}
                          onChange={() => setChosenLeadId(leadId)}
                          disabled={isSending}
                        />
                        <span>Lead #{leadId}</span>
                        <a
                          href={`https://crm.lofty.com/admin/home/lead/detail/${leadId}`}
                          target="_blank"
                          rel="noopener noreferrer"
                        >
                          View ↗
                        </a>
                      </label>
                    ))}
                  </fieldset>
                ) : null}
                {exportStatus.lofty_url ? (
                  <a href={exportStatus.lofty_url} target="_blank" rel="noopener noreferrer">
                    Open in Lofty ↗
                  </a>
                ) : null}
              </div>
            ) : null}

            <section className="lofty-section" aria-label="Lead details">
              <h3>Lead details</h3>
              <dl className="leads-facts">
                <div><dt>Name</dt><dd>{preview.name || "Not on file"}</dd></div>
                <div><dt>Property address</dt><dd>{preview.property_address || "Not on file"}</dd></div>
                <div><dt>Mobile</dt><dd>{preview.phone || "Not on file (omitted)"}</dd></div>
                <div>
                  <dt>Email</dt>
                  <dd>{preview.emails.length ? preview.emails.join(", ") : "Not on file (omitted)"}</dd>
                </div>
              </dl>
            </section>

            <section className="lofty-section">
              <div className="lofty-section-head">
                <h3><label htmlFor="lofty-summary">AI-generated summary</label></h3>
                {preview.available && !summaryLocked ? (
                  <button
                    type="button"
                    className="sms-button-secondary"
                    disabled={isSummarizing || isSending || preview.messages.length === 0}
                    onClick={() => void summarize(preview.conversation_id, preview.messages.length)}
                  >
                    {isSummarizing ? "Generating…" : summary ? "Regenerate" : "Generate summary"}
                  </button>
                ) : null}
              </div>
              <textarea
                id="lofty-summary"
                className="lofty-summary"
                rows={8}
                maxLength={20000}
                value={summary}
                placeholder={isSummarizing ? "Generating summary…" : "Write or generate a summary"}
                disabled={isSending || isSummarizing || summaryLocked || !preview.available}
                onChange={(event) => {
                  setSummary(event.target.value);
                  summaryCache.set(preview.conversation_id, {
                    summary: event.target.value,
                    messageCount: preview.messages.length,
                  });
                }}
              />
              {summaryLocked ? (
                <small>Notes were already started, so the summary saved with them is used.</small>
              ) : (
                <small>Review and edit before sending. The AI only uses what the conversation says.</small>
              )}
              {summaryError ? <p className="sms-error" role="alert">{summaryError}</p> : null}
            </section>

            <section className="lofty-section" aria-label="Full conversation">
              <h3>Full conversation ({preview.messages.length})</h3>
              <ol className="lofty-transcript">
                {preview.messages.map((message) => (
                  <li key={message.id} className={`lofty-line sender-${message.sender.toLowerCase()}`}>
                    <div className="lofty-line-meta">
                      <strong>{message.sender}</strong>
                      <time dateTime={message.created_at ?? undefined}>{stamp(message.created_at)}</time>
                      {message.not_delivered ? <span>not delivered</span> : null}
                    </div>
                    <p>{message.text}</p>
                  </li>
                ))}
              </ol>
            </section>
          </div>
        ) : null}

        {sendError ? <p className="sms-error" role="alert">{sendError}</p> : null}

        <div className="sms-dialog-actions">
          <button type="button" className="sms-button-secondary" onClick={close} disabled={isSending}>
            {isComplete ? "Close" : "Cancel"}
          </button>
          {preview?.available && hasNewMessages ? (
            <button
              type="button"
              className="sms-button-secondary"
              disabled={isSending || isSummarizing}
              onClick={() => void submit(true)}
              title="Adds the latest conversation as new notes on the same Lofty lead"
            >
              {isSending ? "Sending…" : "Resync new messages"}
            </button>
          ) : null}
          {preview?.available && !isComplete ? (
            <button
              type="button"
              className="button"
              disabled={
                isSending ||
                isSummarizing ||
                (!summary.trim() && !summaryLocked)
              }
              onClick={() => void submit(false)}
            >
              {primaryLabel}
            </button>
          ) : null}
        </div>
      </section>
    </div>
  );
}
