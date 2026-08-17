"use client";

import { useEffect, useState } from "react";

import { getAvailability } from "@/features/sms/api/sms-api";
import type { CalendarSlot } from "@/features/sms/types/sms.types";

import type { AppointmentPayload, FollowUp } from "../types/followups.types";

type AppointmentDialogProps = {
  followUp: FollowUp | null;
  accessToken: string;
  onClose: () => void;
  onBook: (payload: AppointmentPayload) => Promise<void>;
};

/**
 * Books one of the broker's own open slots. The slots come from the same
 * availability endpoint Bobbie uses, so a manual booking can never collide with
 * a time she is offering.
 */
export function AppointmentDialog({ followUp, accessToken, onClose, onBook }: AppointmentDialogProps) {
  const [slots, setSlots] = useState<CalendarSlot[]>([]);
  const [timezone, setTimezone] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const isOpen = followUp !== null;

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setIsLoading(true);
    setErrorMessage("");

    getAvailability(accessToken)
      .then((availability) => {
        if (cancelled) return;
        setSlots(availability.slots);
        setTimezone(availability.timezone);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setErrorMessage(
          error instanceof Error ? error.message : "Could not load your open times.",
        );
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, accessToken]);

  if (!followUp) return null;

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSaving) return;

    const form = new FormData(event.currentTarget);
    const chosen = slots.find((slot) => slot.start_at === String(form.get("slot") ?? ""));
    setErrorMessage("");
    if (!chosen) {
      setErrorMessage("Pick one of your open times.");
      return;
    }

    setIsSaving(true);
    try {
      await onBook({
        start_at: chosen.start_at,
        end_at: chosen.end_at,
        title: String(form.get("title") ?? "").trim() || undefined,
        notify: form.get("notify") === "on",
      });
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : "Could not book the appointment.",
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
        aria-labelledby="appointment-dialog-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">SCHEDULE</span>
            <h2 id="appointment-dialog-title">
              Book a meeting with {followUp.name || followUp.contact}
            </h2>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <form onSubmit={handleSubmit}>
          <label className="sms-field">
            <span>Open times{timezone ? ` (${timezone})` : ""}</span>
            <select name="slot" required disabled={isSaving || isLoading || slots.length === 0}>
              {slots.map((slot) => (
                <option key={slot.start_at} value={slot.start_at}>
                  {slot.label}
                </option>
              ))}
            </select>
            <small>
              {isLoading
                ? "Loading your calendar…"
                : slots.length === 0
                  ? "You have no open weekday slots in the booking window."
                  : "Only times that are still free on your calendar are listed."}
            </small>
          </label>

          <label className="sms-field">
            <span>Meeting title</span>
            <input
              name="title"
              maxLength={200}
              defaultValue="Property consultation"
              disabled={isSaving}
            />
          </label>

          <label className="sms-toggle">
            <input type="checkbox" name="notify" defaultChecked disabled={isSaving} />
            <span>
              <strong>Text the owner the confirmation</strong>
              <small>
                Sends the date, time and meeting link to {followUp.contact}. Bobbie will not reply to
                that message.
              </small>
            </span>
          </label>

          {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}

          <div className="sms-dialog-actions">
            <button type="button" className="sms-button-secondary" onClick={onClose} disabled={isSaving}>
              Cancel
            </button>
            <button
              type="submit"
              className="button"
              disabled={isSaving || isLoading || slots.length === 0}
            >
              {isSaving ? "Booking…" : "Book appointment"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
