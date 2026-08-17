"use client";

import { useEffect, useRef, useState } from "react";

export type ConfirmRequest = {
  title: string;
  /** Shown under the title. Keep it to what the action actually does. */
  body: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** Destructive actions get the red confirm button. */
  tone?: "danger" | "default";
  onConfirm: () => Promise<void> | void;
};

type ConfirmDialogProps = {
  request: ConfirmRequest | null;
  onClose: () => void;
};

/**
 * Replaces window.confirm, which cannot be themed and looks like the browser
 * rather than the product. Rendered from a single state slot per screen, so a
 * page only ever has one of these open.
 */
export function ConfirmDialog({ request, onClose }: ConfirmDialogProps) {
  const [isWorking, setIsWorking] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  const isOpen = request !== null;

  // Focus the confirm button so Enter and Escape both work without a mouse.
  useEffect(() => {
    if (!isOpen) {
      setErrorMessage("");
      return;
    }
    confirmRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  if (!request) return null;

  async function handleConfirm(): Promise<void> {
    if (isWorking || !request) return;
    setIsWorking(true);
    setErrorMessage("");
    try {
      await request.onConfirm();
      onClose();
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "That did not work. Please try again.");
    } finally {
      setIsWorking(false);
    }
  }

  return (
    <div className="sms-dialog-scrim" role="presentation" onClick={onClose}>
      <section
        className="sms-dialog confirm-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-dialog-title"
        aria-describedby="confirm-dialog-body"
        onClick={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span className="sms-eyebrow">
              {request.tone === "danger" ? "CONFIRM REMOVAL" : "CONFIRM"}
            </span>
            <h2 id="confirm-dialog-title">{request.title}</h2>
          </div>
          <button type="button" className="sms-icon-button" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </header>

        <p id="confirm-dialog-body" className="confirm-dialog-body">{request.body}</p>

        {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}

        <div className="sms-dialog-actions">
          <button type="button" className="sms-button-secondary" onClick={onClose} disabled={isWorking}>
            {request.cancelLabel ?? "Cancel"}
          </button>
          <button
            ref={confirmRef}
            type="button"
            className={`button${request.tone === "danger" ? " button-danger" : ""}`}
            onClick={handleConfirm}
            disabled={isWorking}
          >
            {isWorking ? "Working…" : request.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </section>
    </div>
  );
}
