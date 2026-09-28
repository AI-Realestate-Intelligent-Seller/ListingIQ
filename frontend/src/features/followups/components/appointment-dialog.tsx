"use client";

import dynamic from "next/dynamic";
import { useEffect, useState } from "react";

import { getAvailability } from "@/features/sms/api/sms-api";
import type { CalendarSlot } from "@/features/sms/types/sms.types";

import type { AppointmentPayload, FollowUp } from "../types/followups.types";
import type { GeocodedAppointmentAddress } from "../types/followups.types";
import { geocodeAppointmentAddress } from "../api/followups-api";

const AppointmentLocationMap = dynamic(
  () => import("./appointment-location-map").then((module) => module.AppointmentLocationMap),
  { ssr: false, loading: () => <div className="appointment-map-loading">Loading map…</div> },
);

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
  const [address, setAddress] = useState("");
  const [resolvedAddress, setResolvedAddress] = useState<GeocodedAppointmentAddress | null>(null);
  const [isGeocoding, setIsGeocoding] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const isOpen = followUp !== null;

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setIsLoading(true);
    setAddress("");
    setResolvedAddress(null);
    setIsGeocoding(false);
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

  async function previewAddress(): Promise<void> {
    const exactAddress = address.trim();
    setErrorMessage("");
    if (!exactAddress) {
      setErrorMessage("Enter the exact appointment address.");
      return;
    }

    setIsGeocoding(true);
    try {
      setResolvedAddress(await geocodeAppointmentAddress(exactAddress, accessToken));
    } catch (error) {
      setResolvedAddress(null);
      setErrorMessage(
        error instanceof Error ? error.message : "Could not find that address.",
      );
    } finally {
      setIsGeocoding(false);
    }
  }

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
    if (!resolvedAddress || resolvedAddress.address !== address.trim()) {
      setErrorMessage("Preview the exact address on the map before booking.");
      return;
    }

    setIsSaving(true);
    try {
      await onBook({
        start_at: chosen.start_at,
        end_at: chosen.end_at,
        title: String(form.get("title") ?? "").trim() || undefined,
        address: resolvedAddress.address,
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

          <label className="sms-field">
            <span>Exact appointment address</span>
            <div className="appointment-address-row">
              <input
                name="address"
                value={address}
                maxLength={500}
                autoComplete="street-address"
                placeholder="123 Main St, City, State ZIP"
                disabled={isSaving}
                onChange={(event) => {
                  setAddress(event.target.value);
                  setResolvedAddress(null);
                }}
              />
              <button
                type="button"
                className="sms-button-secondary"
                disabled={isSaving || isGeocoding || !address.trim()}
                onClick={() => void previewAddress()}
              >
                {isGeocoding ? "Finding…" : "Preview on map"}
              </button>
            </div>
            <small>Enter the full street address, then verify the pin before booking.</small>
          </label>

          {resolvedAddress ? (
            <div className="appointment-map-preview">
              <AppointmentLocationMap
                address={resolvedAddress.display_name}
                latitude={resolvedAddress.latitude}
                longitude={resolvedAddress.longitude}
              />
              <p>
                <strong>Map match</strong>
                <span>{resolvedAddress.display_name}</span>
              </p>
            </div>
          ) : null}

          <label className="sms-toggle">
            <input type="checkbox" name="notify" defaultChecked disabled={isSaving} />
            <span>
              <strong>Text the owner the confirmation</strong>
              <small>
                Sends the date, time, exact address and map link to {followUp.contact}. Bobbie will not reply to
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
              disabled={isSaving || isLoading || isGeocoding || slots.length === 0 || !resolvedAddress}
            >
              {isSaving ? "Booking…" : "Book appointment"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
