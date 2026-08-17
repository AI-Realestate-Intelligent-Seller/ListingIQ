"use client";

import { useState } from "react";

import type { NewConversationPayload } from "../types/sms.types";

type NewConversationDialogProps = {
  open: boolean;
  onClose: () => void;
  onCreate: (payload: NewConversationPayload) => Promise<void>;
};

const E164 = /^\+[1-9]\d{6,14}$/;

export function NewConversationDialog({ open, onClose, onCreate }: NewConversationDialogProps) {
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  if (!open) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSaving) return;

    const form = new FormData(event.currentTarget);
    const contact = String(form.get("contact") ?? "").replace(/\s/g, "");
    setErrorMessage("");

    if (!E164.test(contact)) {
      setErrorMessage("Use international E.164 format, including the country code (e.g. +13125550123).");
      return;
    }

    setIsSaving(true);
    try {
      await onCreate({
        contact,
        name: String(form.get("name") ?? "").trim(),
        property_address: String(form.get("property_address") ?? "").trim(),
        outreach_reason: String(form.get("outreach_reason") ?? "").trim(),
        ai_enabled: form.get("ai_enabled") === "on",
      });
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not start the conversation.",
      );
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
        aria-labelledby="sms-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">NEW CONVERSATION</span>
            <h2 id="sms-dialog-title">Start an outreach</h2>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <form onSubmit={handleSubmit}>
          <label className="sms-field">
            <span>Owner name</span>
            <input name="name" placeholder="e.g. Maya Chen" maxLength={120} required disabled={isSaving} />
          </label>

          <label className="sms-field">
            <span>Phone number</span>
            <input
              name="contact"
              type="tel"
              placeholder="+13125550123"
              autoComplete="tel"
              required
              disabled={isSaving}
            />
            <small>International E.164 format, including the country code.</small>
          </label>

          <label className="sms-field">
            <span>Property address</span>
            <input
              name="property_address"
              placeholder="e.g. 123 Maple Ave, Austin, TX"
              maxLength={300}
              required
              disabled={isSaving}
            />
          </label>

          <label className="sms-field">
            <span>Outreach reason</span>
            <textarea
              name="outreach_reason"
              rows={3}
              maxLength={300}
              placeholder="e.g. The property appears to have come off the market without a recorded sale."
              required
              disabled={isSaving}
            />
            <small>Bobbie uses this exact reason to explain why she reached out.</small>
          </label>

          <label className="sms-toggle">
            <input type="checkbox" name="ai_enabled" defaultChecked disabled={isSaving} />
            <span>
              <strong>Let Bobbie handle it</strong>
              <small>
                She sends the introduction now and answers the owner&apos;s replies. Turn this off to
                write every message yourself.
              </small>
            </span>
          </label>

          {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}

          <div className="sms-dialog-actions">
            <button type="button" className="sms-button-secondary" onClick={onClose} disabled={isSaving}>
              Cancel
            </button>
            <button type="submit" className="button" disabled={isSaving}>
              {isSaving ? "Starting…" : "Start conversation"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
