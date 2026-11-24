"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { updateExperimentFollowup } from "../api/experiments-api";
import type { Experiment, ExperimentVariant } from "../types/experiments.types";

const DEFAULT_FOLLOWUP = "Hi {{first_name}}, just following up about {{address}}. Would you be open to a brief conversation?";

function scheduledLabel(value: string, timeZone: string): string {
  const stamp = new Date(/[Z+]$/.test(value) ? value : `${value}Z`);
  try { return stamp.toLocaleString([], { timeZone, dateStyle: "medium", timeStyle: "short" }); }
  catch { return stamp.toLocaleString(); }
}

function VariantFollowupEditor({ experiment, variant, token, onSaved, onError }: {
  experiment: Experiment;
  variant: ExperimentVariant;
  token: string;
  onSaved: (updated: Experiment) => void;
  onError: (message: string) => void;
}) {
  const [enabled, setEnabled] = useState(variant.followup_enabled);
  const [message, setMessage] = useState(variant.followup_template || DEFAULT_FOLLOWUP);
  const [delay, setDelay] = useState(variant.followup_delay_hours ?? 48);
  const [saving, setSaving] = useState(false);
  const editable = variant.followup_editable !== false;

  async function save() {
    setSaving(true);
    onError("");
    try {
      const updated = await updateExperimentFollowup(experiment.id, variant.campaign_id, {
        followup_enabled: enabled,
        followup_template: message,
        followup_delay_hours: delay,
      }, token);
      onSaved(updated);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "Could not update the follow-up.");
    } finally {
      setSaving(false);
    }
  }

  return <article className="experiment-launched-followup-card">
    <header><div><strong>{variant.label}</strong><small>{variant.lead_count} leads</small></div>{variant.followup_scheduled_at ? <span>Next: {scheduledLabel(variant.followup_scheduled_at, experiment.timezone)}</span> : null}</header>
    {editable ? <>
      <label className="toggle"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /><span><strong>Send follow-up</strong><small>Only owners who have not replied</small></span></label>
      {enabled ? <><label><span>Follow-up message</span><textarea aria-label={`${variant.label} follow-up message`} value={message} onChange={(event) => setMessage(event.target.value)} /></label><label className="experiment-launched-followup-delay"><span>Send after</span><input aria-label={`${variant.label} send after`} type="number" min="1" max="720" value={delay} onChange={(event) => setDelay(Number(event.target.value))} /><span>hours after initial outreach</span></label></> : null}
      <button type="button" disabled={saving || (enabled && (!message.trim() || delay < 1 || delay > 720))} onClick={() => void save()}>{saving ? "Saving…" : "Save follow-up"}</button>
    </> : <p>This follow-up has already started and can no longer be edited.</p>}
  </article>;
}

export function ExperimentFollowupEditor({ experiment, token, onSaved, onError, onClose }: {
  experiment: Experiment;
  token: string;
  onSaved: (updated: Experiment) => void;
  onError: (message: string) => void;
  onClose?: () => void;
}) {
  useEffect(() => {
    if (!onClose) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);

  if (experiment.status !== "running") return null;
  const editor = <section className="experiment-launched-followups" role={onClose ? "dialog" : undefined} aria-modal={onClose ? "true" : undefined} aria-labelledby={onClose ? "experiment-followup-editor-title" : undefined}>
    <header><span>FOLLOW-UP SCHEDULE</span><h3 id={onClose ? "experiment-followup-editor-title" : undefined}>Edit before the first follow-up sends</h3><p>Timing is calculated from each initial outreach. Owners who reply are skipped.</p>{onClose ? <button type="button" aria-label="Close follow-up editor" onClick={onClose}>Close</button> : null}</header>
    <div>{experiment.variants.map((variant) => <VariantFollowupEditor key={variant.campaign_id} experiment={experiment} variant={variant} token={token} onSaved={onSaved} onError={onError} />)}</div>
  </section>;

  if (!onClose || typeof document === "undefined") return editor;
  return createPortal(
    <div className="experiment-followup-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="experiment-followup-dialog">{editor}</div>
    </div>,
    document.body,
  );
}
