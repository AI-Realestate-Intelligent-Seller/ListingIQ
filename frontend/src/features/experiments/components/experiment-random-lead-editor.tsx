"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { addExperimentRandomLeads } from "../api/experiments-api";
import type { Experiment } from "../types/experiments.types";

export function ExperimentRandomLeadEditor({ experiment, token, onSaved, onError, onClose }: {
  experiment: Experiment;
  token: string;
  onSaved: (updated: Experiment) => void;
  onError: (message: string) => void;
  onClose: () => void;
}) {
  const [counts, setCounts] = useState<Record<number, string>>({});
  const [savingCampaignId, setSavingCampaignId] = useState<number | null>(null);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && savingCampaignId === null) onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [onClose, savingCampaignId]);

  async function add(campaignId: number, label: string) {
    const count = Math.floor(Number(counts[campaignId] ?? "1"));
    if (!Number.isFinite(count) || count < 1 || count > 10000) {
      onError("Choose between 1 and 10,000 random leads.");
      return;
    }
    setSavingCampaignId(campaignId);
    setNotice("");
    onError("");
    try {
      const before = experiment.variants.find((variant) => variant.campaign_id === campaignId)?.lead_count ?? 0;
      const updated = await addExperimentRandomLeads(experiment.id, campaignId, count, token);
      const after = updated.variants.find((variant) => variant.campaign_id === campaignId)?.lead_count ?? before;
      onSaved(updated);
      setNotice(`${Math.max(after - before, 0)} random leads added to ${label}. Their outreach is queued.`);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "Could not add random leads.");
    } finally {
      setSavingCampaignId(null);
    }
  }

  if (experiment.status !== "running" || typeof document === "undefined") return null;
  return createPortal(
    <div className="experiment-followup-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && savingCampaignId === null) onClose(); }}>
      <div className="experiment-followup-dialog experiment-random-lead-dialog">
        <section role="dialog" aria-modal="true" aria-labelledby="experiment-random-lead-title">
          <header><div><span>EXPERIMENT AUDIENCE</span><h3 id="experiment-random-lead-title">Add random leads after launch</h3><p>Choose a variant. Eligible leads are selected randomly from the Lead Pool and immediately queued with that variant’s opening message.</p></div><button type="button" disabled={savingCampaignId !== null} onClick={onClose}>Close</button></header>
          <div>{experiment.variants.map((variant) => {
            const saving = savingCampaignId === variant.campaign_id;
            return <article key={variant.campaign_id}><div><strong>{variant.label}</strong><small>{variant.lead_count.toLocaleString()} leads currently</small></div><label><span>Number of random leads</span><input aria-label={`${variant.label} random leads to add`} type="number" min="1" max="10000" value={counts[variant.campaign_id] ?? "1"} onChange={(event) => setCounts((values) => ({ ...values, [variant.campaign_id]: event.target.value }))} /></label><button type="button" disabled={savingCampaignId !== null} onClick={() => void add(variant.campaign_id, variant.label)}>{saving ? "Adding…" : "Add and send outreach"}</button></article>;
          })}</div>
          {notice ? <p className="experiment-audience-notice" role="status">{notice}</p> : null}
        </section>
      </div>
    </div>,
    document.body,
  );
}
