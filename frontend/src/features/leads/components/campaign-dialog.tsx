"use client";

import { useState } from "react";

type CampaignDialogProps = {
  open: boolean;
  count: number;
  onClose: () => void;
  onConfirm: (outreachReason: string) => Promise<void>;
};

/**
 * Confirms handing a selection of leads to Bobbie. The reason is optional:
 * left blank, each lead's own signal supplies one, which keeps Bobbie's
 * opening line factual for a mixed selection.
 */
export function CampaignDialog({ open, count, onClose, onConfirm }: CampaignDialogProps) {
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  if (!open) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSaving) return;
    const reason = String(new FormData(event.currentTarget).get("outreach_reason") ?? "");

    setIsSaving(true);
    setErrorMessage("");
    try {
      await onConfirm(reason);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "Could not start the campaign.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="sms-dialog-scrim" role="presentation" onClick={onClose}>
      <section
        className="sms-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="campaign-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">NEW CAMPAIGN</span>
            <h2 id="campaign-dialog-title">
              Hand {count} {count === 1 ? "lead" : "leads"} to Bobbie
            </h2>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <form onSubmit={handleSubmit}>
          <p className="leads-dialog-note">
            Bobbie sends each owner an introduction and answers their replies. The
            conversations appear in the SMS tab, where you can take over at any time.
          </p>

          <label className="sms-field">
            <span>Outreach reason (optional)</span>
            <textarea
              name="outreach_reason"
              rows={3}
              maxLength={300}
              placeholder="Leave blank to use each lead's own signal"
              disabled={isSaving}
            />
            <small>
              Blank is usually best for a mixed selection: an expired listing and a
              probate lead need different opening lines.
            </small>
          </label>

          {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}

          <div className="sms-dialog-actions">
            <button type="button" className="sms-button-secondary" onClick={onClose} disabled={isSaving}>
              Cancel
            </button>
            <button className="button" type="submit" disabled={isSaving}>
              {isSaving ? "Starting…" : "Start campaign"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
