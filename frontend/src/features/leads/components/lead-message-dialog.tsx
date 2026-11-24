"use client";

import { useMemo, useState } from "react";

import type { Lead, LeadMessageResult } from "../types/leads.types";

type LeadMessageDialogProps = {
  lead: Lead;
  onClose: () => void;
  /** Resolves with what the API actually sent, so the pool can report it. */
  onSend: (text: string, phones: string[]) => Promise<LeadMessageResult>;
};

const MAX_CHARS = 1600;

/**
 * Write one message to a single lead and start the thread on every number
 * chosen. Every number the lead has is offered, because an owner reachable at
 * two phones is one person the broker is trying to get hold of; do-not-contact
 * numbers are listed but cannot be selected.
 */
export function LeadMessageDialog({ lead, onClose, onSend }: LeadMessageDialogProps) {
  const numbers = useMemo(() => {
    const rows = lead.phone_numbers.length
      ? lead.phone_numbers
      : lead.phone
        ? [{ phone: lead.phone, dnc: false }]
        : [];
    return rows;
  }, [lead.phone, lead.phone_numbers]);
  const contactable = useMemo(
    () => numbers.filter((row) => !row.dnc).map((row) => row.phone),
    [numbers],
  );

  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string[]>(contactable);
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const owner = lead.owner_name || lead.phone || "this owner";
  const canSend = Boolean(text.trim()) && selected.length > 0 && !isSending;

  function toggleNumber(phone: string): void {
    setSelected((current) =>
      current.includes(phone) ? current.filter((item) => item !== phone) : [...current, phone],
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!canSend) return;
    setErrorMessage("");
    setIsSending(true);
    try {
      await onSend(text.trim(), selected);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not send the message.");
    } finally {
      setIsSending(false);
    }
  }

  return (
    <div className="sms-dialog-scrim" role="presentation" onClick={onClose}>
      <section
        className="sms-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lead-message-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">INDIVIDUALLY CONTACTED</span>
            <h2 id="lead-message-title">Message {owner}</h2>
            <p className="leads-message-address">
              {lead.property_address || "No address on file"}
            </p>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <form onSubmit={handleSubmit}>
          <fieldset className="leads-message-numbers">
            <legend>Send to</legend>
            {numbers.length === 0 ? (
              <p className="sms-error" role="alert">
                This lead has no phone number on file.
              </p>
            ) : (
              numbers.map((row) => (
                <label
                  key={row.phone}
                  className={`leads-message-number${row.dnc ? " blocked" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(row.phone)}
                    disabled={row.dnc || isSending}
                    onChange={() => toggleNumber(row.phone)}
                  />
                  <span>
                    <strong>{row.phone}</strong>
                    {row.dnc ? (
                      <small>Do not contact</small>
                    ) : (
                      <small>
                        {[row.owner_name, row.type].filter(Boolean).join(" · ") || "Contactable"}
                      </small>
                    )}
                  </span>
                </label>
              ))
            )}
            <small className="leads-message-hint">
              {selected.length > 1
                ? `The same message goes to each of the ${selected.length} numbers, as its own thread.`
                : "The message opens one thread you can carry on in the SMS tab."}
            </small>
          </fieldset>

          <label className="sms-field">
            <span>Message</span>
            <textarea
              rows={5}
              maxLength={MAX_CHARS}
              value={text}
              onChange={(event) => setText(event.target.value)}
              placeholder={`Hi ${(lead.owner_name || "").split(" ")[0] || "there"}, I work with owners near ${lead.area || "you"} and wanted to reach out directly.`}
              disabled={isSending}
              required
            />
          </label>
          {/* Outside the label, so the field's accessible name stays "Message". */}
          <small className="leads-message-hint">
            {text.trim().length}/{MAX_CHARS} characters. Placeholders such as{" "}
            <code>{"{{first_name}}"}</code> and <code>{"{{address}}"}</code> are filled in for
            this lead.
          </small>

          {errorMessage ? (
            <p className="sms-error" role="alert">
              {errorMessage}
            </p>
          ) : null}

          <div className="sms-dialog-actions">
            <button
              type="button"
              className="sms-button-secondary"
              onClick={onClose}
              disabled={isSending}
            >
              Cancel
            </button>
            <button className="button" type="submit" disabled={!canSend}>
              {isSending
                ? "Sending…"
                : `Send${selected.length > 1 ? ` to ${selected.length} numbers` : ""}`}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
