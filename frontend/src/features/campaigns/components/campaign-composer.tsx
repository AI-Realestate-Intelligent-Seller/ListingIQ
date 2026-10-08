"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { LeadDetailDrawer } from "@/features/leads/components/lead-detail-drawer";

import type {
  CampaignDetail,
  CampaignReasonSuggestions,
  CampaignSendResult,
} from "../types/campaigns.types";
import { sendCampaign, suggestReasons, updateCampaign } from "../api/campaigns-api";

type CampaignComposerProps = {
  campaign: CampaignDetail;
  accessToken: string;
  /** Replaces the draft in the parent as the server re-renders the preview. */
  onChange: (campaign: CampaignDetail) => void;
  onSent: (result: CampaignSendResult) => void;
  onDiscard: () => void;
  onBack: () => void;
  /** Opens one recipient's thread wherever threads are read. */
  onOpenConversation: (conversationId: number) => void;
};

/** Placeholders are inserted at the cursor, so nobody has to type the braces. */
const TOKEN_ORDER = [
  "first_name",
  "owner_name",
  "address",
  "area",
  "reason",
  "brokerage",
  "agent_name",
];

function isDeliveryFailure(status: string | null): boolean {
  return /fail|reject|undeliver|timeout|unconfirmed/i.test(status ?? "");
}

function deliveryFailureReason(status: string | null, providerReason: string | null): string {
  if (providerReason) return providerReason;
  if (/timeout/i.test(status ?? "")) return "Carrier delivery confirmation timed out.";
  if (/unconfirmed/i.test(status ?? "")) return "The carrier could not confirm delivery.";
  if (/sending_failed/i.test(status ?? "")) return "The provider could not send the message.";
  return "The carrier rejected the destination.";
}

/**
 * Writes one campaign: its name, its message, and the per-recipient preview of
 * what that message becomes.
 *
 * The preview is rendered by the server rather than in the browser. The same
 * code then does the sending, so what the broker reads here is exactly what
 * two hundred owners receive — a second, client-side renderer would be a second
 * chance to disagree.
 */
export function CampaignComposer({
  campaign,
  accessToken,
  onChange,
  onSent,
  onDiscard,
  onBack,
  onOpenConversation,
}: CampaignComposerProps) {
  const [name, setName] = useState(campaign.name);
  const [template, setTemplate] = useState(campaign.message_template);
  const [reason, setReason] = useState(campaign.outreach_reason);
  const [ideas, setIdeas] = useState<CampaignReasonSuggestions | null>(null);
  const [isSuggesting, setIsSuggesting] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  /** The recipient whose property details are open, as in the Lead Pool. */
  const [detailId, setDetailId] = useState<number | null>(null);
  const editorRef = useRef<HTMLTextAreaElement | null>(null);

  const isSent = campaign.status === "sent";
  const preview = campaign.preview;
  const recipients = preview.recipients;

  // Re-render the preview a beat after typing stops rather than on every key:
  // each save is a round trip that re-renders every recipient's message.
  useEffect(() => {
    if (isSent) return;
    if (
      name === campaign.name &&
      template === campaign.message_template &&
      reason === campaign.outreach_reason
    ) {
      return;
    }

    const timer = setTimeout(() => {
      setIsSaving(true);
      setErrorMessage("");
      updateCampaign(
        campaign.id,
        { name, message_template: template, outreach_reason: reason },
        accessToken,
      )
        .then(onChange)
        .catch((error: unknown) => {
          // A template the server rejects is normal while typing — it comes back
          // as a sentence, and Send stays disabled until it renders.
          setErrorMessage(error instanceof Error ? error.message : "Could not save the draft.");
        })
        .finally(() => setIsSaving(false));
    }, 600);
    return () => clearTimeout(timer);
  }, [
    name,
    template,
    reason,
    campaign.id,
    campaign.name,
    campaign.message_template,
    campaign.outreach_reason,
    accessToken,
    isSent,
    onChange,
  ]);

  /** Asks the server what this set of leads has in common and how to say it. */
  async function fetchIdeas(): Promise<void> {
    if (isSuggesting) return;
    setIsSuggesting(true);
    setErrorMessage("");
    try {
      setIdeas(await suggestReasons(campaign.id, accessToken));
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not suggest a reason.",
      );
    } finally {
      setIsSuggesting(false);
    }
  }

  /** Drops `{{token}}` where the cursor is, or at the end if the box is unfocused. */
  function insertToken(token: string): void {
    const editor = editorRef.current;
    const snippet = `{{${token}}}`;
    if (!editor) {
      setTemplate((current) => `${current}${snippet}`);
      return;
    }
    const start = editor.selectionStart ?? template.length;
    const end = editor.selectionEnd ?? template.length;
    const next = `${template.slice(0, start)}${snippet}${template.slice(end)}`;
    setTemplate(next);
    // Put the caret after the inserted token so typing continues naturally.
    requestAnimationFrame(() => {
      editor.focus();
      editor.setSelectionRange(start + snippet.length, start + snippet.length);
    });
  }

  async function handleSend(): Promise<void> {
    if (isSending || isSent) return;
    setIsSending(true);
    setErrorMessage("");
    try {
      onSent(await sendCampaign(campaign.id, accessToken));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not send the campaign.");
    } finally {
      setIsSending(false);
    }
  }

  const longCount = useMemo(
    () => recipients.filter((item) => item.is_long).length,
    [recipients],
  );
  // Edits that have not reached the server yet are not in the preview, and the
  // send uses the *stored* template — so Send waits for the draft to settle.
  // A rejected template never saves, which is exactly when this stays true.
  const isDirty =
    name !== campaign.name ||
    template !== campaign.message_template ||
    reason !== campaign.outreach_reason;
  const canSend =
    !isSent && recipients.length > 0 && !preview.template_error && !isSaving && !isDirty;

  return (
    <section className="campaign-composer">
      <header className="campaigns-header">
        <div>
          <button type="button" className="campaigns-back" onClick={onBack}>
            ← All campaigns
          </button>
          <span className="sms-eyebrow">{isSent ? "SENT CAMPAIGN" : "NEW CAMPAIGN"}</span>
          <h2>{isSent ? campaign.name : "Write the opening message"}</h2>
          <p>
            {isSent
              ? `${campaign.replied} of ${campaign.delivered} ${
                  campaign.delivered === 1 ? "owner has" : "owners have"
                } replied. These leads live here now, not in the Lead Pool.`
              : "Every recipient gets this message with their own address and reason filled in."}
          </p>
        </div>
        {!isSent ? (
          <div className="campaign-composer-actions">
            <button type="button" className="sms-button-secondary" onClick={onDiscard}>
              Discard draft
            </button>
            <button
              className="button"
              type="button"
              onClick={() => void handleSend()}
              disabled={!canSend || isSending}
              title={
                preview.template_error ||
                (recipients.length === 0
                  ? "No lead in this draft can be texted."
                  : isDirty
                    ? "Saving your changes…"
                    : undefined)
              }
            >
              {isSending
                ? "Sending…"
                : `Send to ${recipients.length} ${recipients.length === 1 ? "lead" : "leads"}`}
            </button>
          </div>
        ) : null}
      </header>

      <div className="campaign-composer-layout">
        <div className="campaign-editor">
          <label className="sms-field">
            <span>Campaign name</span>
            <input
              value={name}
              maxLength={200}
              disabled={isSent}
              onChange={(event) => setName(event.target.value)}
              placeholder="Expired listings — Hoffman Estates"
            />
          </label>

          <div className="campaign-reason">
            <div className="campaign-reason-head">
              <label htmlFor="campaign-reason-input">Reason</label>
              {!isSent ? (
                <button
                  type="button"
                  className="sms-button-secondary campaign-suggest"
                  onClick={() => void fetchIdeas()}
                  disabled={isSuggesting}
                >
                  {isSuggesting ? "Thinking…" : "✦ Suggest from signals"}
                </button>
              ) : null}
            </div>
            <textarea
              id="campaign-reason-input"
              value={reason}
              rows={2}
              maxLength={300}
              disabled={isSent}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Leave blank to use each lead's own signal"
            />
            <small className="campaigns-note">
              This fills {"{{reason}}"} for every recipient. Blank is right for a mixed
              selection: an expired listing and a probate lead need different wording.
            </small>

            {ideas ? (
              <div className="campaign-ideas">
                {ideas.signals.length > 0 ? (
                  <p className="campaign-ideas-signals">
                    {ideas.signals.map((item) => (
                      <span key={item.key} className="campaign-signal-chip">
                        {item.label} · {Math.round(item.share * 100)}%
                      </span>
                    ))}
                  </p>
                ) : null}
                {ideas.note ? <p className="campaigns-note">{ideas.note}</p> : null}
                <ul className="campaign-ideas-list">
                  {ideas.suggestions.map((text) => (
                    <li key={text}>
                      <button type="button" onClick={() => setReason(text)}>
                        {text}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          <label className="sms-field">
            <span>Message</span>
            <textarea
              ref={editorRef}
              value={template}
              rows={7}
              maxLength={1600}
              disabled={isSent}
              onChange={(event) => setTemplate(event.target.value)}
            />
          </label>

          {!isSent ? (
            <>
              <div className="campaign-tokens">
                <span className="campaign-tokens-label">Insert:</span>
                {TOKEN_ORDER.filter((token) => token in preview.tokens).map((token) => (
                  <button
                    key={token}
                    type="button"
                    className="campaign-token"
                    title={preview.tokens[token]}
                    onClick={() => insertToken(token)}
                  >
                    {`{{${token}}}`}
                  </button>
                ))}
              </div>
              <p className="campaigns-note">
                {isSaving ? "Saving…" : "Each placeholder is filled from the lead before sending."}
              </p>
            </>
          ) : null}

          {preview.template_error ? (
            <p className="sms-error" role="alert">{preview.template_error}</p>
          ) : null}
          {errorMessage && errorMessage !== preview.template_error ? (
            <p className="sms-error" role="alert">{errorMessage}</p>
          ) : null}
          {longCount > 0 ? (
            <p className="campaigns-warning">
              {longCount} {longCount === 1 ? "message runs" : "messages run"} past two SMS
              segments and will be billed as three.
            </p>
          ) : null}

          {preview.skipped.length > 0 ? (
            <div className="campaign-skipped">
              <strong>
                {preview.skipped.length} {preview.skipped.length === 1 ? "lead" : "leads"} will not
                be texted
              </strong>
              <ul>
                {preview.skipped.map((item) => (
                  <li key={item.lead_id}>
                    <span>{item.owner_name ?? `Lead ${item.lead_id}`}</span>
                    <em>{item.reason}</em>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

      </div>

      <div className="campaign-people">
        <div className="campaign-people-head">
          <h3>{isSent ? "Who was contacted" : "Who this will reach"}</h3>
          <span className="sms-count">{recipients.length}</span>
        </div>

        {recipients.length === 0 ? (
          <p className="sms-muted">
            {isSent
              ? "This campaign did not reach anyone."
              : "No lead in this draft can be texted. Go back to the Lead Pool and pick another selection."}
          </p>
        ) : (
          <div className="leads-table-wrap">
            <table className="leads-table campaign-table">
              <thead>
                <tr>
                  <th scope="col">Owner / Property</th>
                  <th scope="col">Signals</th>
                  <th scope="col" className="leads-numeric">Score</th>
                  <th scope="col">{isSent ? "Message sent" : "Message"}</th>
                  <th scope="col">{isSent ? "Reply" : "Length"}</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {recipients.map((item) => (
                  <tr key={item.lead_id}>
                    <th scope="row">
                      <strong>{item.owner_name ?? "Unnamed owner"}</strong>
                      <small>{[item.property_address, item.area].filter(Boolean).join(" · ")}</small>
                    </th>
                    <td>
                      <span className="leads-signals">
                        {item.signals.length === 0 ? (
                          <span className="leads-none">—</span>
                        ) : (
                          item.signals.map((signal) => (
                            <span key={signal.key} className="leads-signal">{signal.label}</span>
                          ))
                        )}
                      </span>
                    </td>
                    <td className="leads-numeric">{item.score}</td>
                    <td className="campaign-message-cell">{item.text}</td>
                    <td>
                      {isSent ? (
                        <span
                          className={`campaign-reply-badge${
                            item.replied
                              ? " replied"
                              : isDeliveryFailure(item.delivery_status)
                                ? " failed"
                                : ""
                          }`}
                          title={item.delivery_failure_reason ?? undefined}
                        >
                          {item.replied
                            ? "Replied"
                            : isDeliveryFailure(item.delivery_status)
                              ? `Not delivered — ${deliveryFailureReason(
                                  item.delivery_status,
                                  item.delivery_failure_reason,
                                )}`
                              : "No reply yet"}
                        </span>
                      ) : (
                        <small className={item.is_long ? "campaign-long" : undefined}>
                          {item.text.length} chars
                          {item.is_long ? " · 3 segments" : null}
                        </small>
                      )}
                    </td>
                    <td className="campaign-open-cell">
                      <button
                        type="button"
                        className="sms-icon-button"
                        onClick={() => setDetailId(item.lead_id)}
                        aria-label={`View details for ${item.owner_name ?? "this owner"}`}
                        title="View property details"
                      >
                        ⓘ
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* The same panel the Lead Pool opens: a lead handed to a campaign leaves
          the pool, so this is the only place its property record can be read. */}
      <LeadDetailDrawer
        leadId={detailId}
        accessToken={accessToken}
        onClose={() => setDetailId(null)}
        onOpenConversation={(conversationId) => {
          setDetailId(null);
          onOpenConversation(conversationId);
        }}
      />
    </section>
  );
}
