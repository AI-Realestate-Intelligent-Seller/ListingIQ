"use client";

import { useEffect, useRef, useState } from "react";

import { getExperiment, renameExperiment } from "../api/experiments-api";
import type { Experiment } from "../types/experiments.types";

export function ExperimentDetailMenu({ experiment, token, onEditFollowup, onAddRandomLeads, onRenamed, onError }: {
  experiment: Experiment;
  token: string;
  onEditFollowup: () => void;
  onAddRandomLeads: () => void;
  onRenamed: (updated: Experiment) => void;
  onError: (message: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(experiment.name);
  const [saving, setSaving] = useState(false);
  const [outreachOpen, setOutreachOpen] = useState(false);
  const [outreachDetail, setOutreachDetail] = useState<Experiment | null>(null);
  const [outreachLoading, setOutreachLoading] = useState(false);
  const [outreachError, setOutreachError] = useState("");
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  useEffect(() => {
    if (!outreachOpen) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOutreachOpen(false);
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [outreachOpen]);

  async function openOutreachDetail() {
    setOpen(false);
    setOutreachOpen(true);
    setOutreachLoading(true);
    setOutreachError("");
    try {
      setOutreachDetail(await getExperiment(experiment.id, token));
    } catch (cause) {
      setOutreachError(cause instanceof Error ? cause.message : "Could not load outreach details.");
    } finally {
      setOutreachLoading(false);
    }
  }

  async function saveName() {
    if (!name.trim()) return;
    setSaving(true);
    onError("");
    try {
      const updated = await renameExperiment(experiment.id, name.trim(), token);
      onRenamed(updated);
      setRenaming(false);
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : "Could not rename the experiment.");
    } finally {
      setSaving(false);
    }
  }

  return <>
    <div className="analysis-experiment-menu" ref={menuRef}>
      <button type="button" className="analysis-experiment-menu-trigger" aria-label="Experiment actions" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>•••</button>
      {open ? <div className="analysis-experiment-menu-options" role="menu">
        <button type="button" role="menuitem" onClick={() => void openOutreachDetail()}>Outreach details</button>
        {experiment.status === "running" ? <button type="button" role="menuitem" onClick={() => { setOpen(false); onEditFollowup(); }}>Edit follow-up details</button> : null}
        {experiment.status === "running" ? <button type="button" role="menuitem" onClick={() => { setOpen(false); onAddRandomLeads(); }}>Add random leads</button> : null}
        <button type="button" role="menuitem" onClick={() => { setName(experiment.name); setOpen(false); setRenaming(true); }}>Rename experiment</button>
      </div> : null}
    </div>
    {renaming ? <div className="analysis-rename-backdrop" role="presentation">
      <form className="analysis-rename-dialog" role="dialog" aria-modal="true" aria-labelledby="analysis-rename-title" onSubmit={(event) => { event.preventDefault(); void saveName(); }}>
        <div><span>EXPERIMENT</span><h3 id="analysis-rename-title">Rename experiment</h3><p>Variant campaign names will be updated automatically.</p></div>
        <label><span>Experiment name</span><input autoFocus maxLength={200} value={name} onChange={(event) => setName(event.target.value)} /></label>
        <footer><button type="button" onClick={() => setRenaming(false)}>Cancel</button><button type="submit" className="button" disabled={saving || !name.trim()}>{saving ? "Saving…" : "Save name"}</button></footer>
      </form>
    </div> : null}
    {outreachOpen ? <div className="analysis-outreach-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setOutreachOpen(false); }}>
      <section className="analysis-outreach-dialog" role="dialog" aria-modal="true" aria-labelledby="analysis-outreach-title">
        <header><div><span>OUTREACH DETAILS</span><h3 id="analysis-outreach-title">{experiment.name}</h3><p>See exactly how the selected audience became completed outreach.</p></div><button type="button" aria-label="Close outreach details" onClick={() => setOutreachOpen(false)}>×</button></header>
        {outreachLoading ? <p className="analysis-outreach-state">Loading outreach details…</p> : null}
        {outreachError ? <p className="analysis-outreach-error" role="alert">{outreachError}</p> : null}
        {!outreachLoading && !outreachError && outreachDetail ? <OutreachDetail experiment={outreachDetail} /> : null}
      </section>
    </div> : null}
  </>;
}

function OutreachDetail({ experiment }: { experiment: Experiment }) {
  const notContacted = experiment.not_contacted ?? [];
  const outreached = experiment.outreached_leads ?? 0;
  const unavailableCount = experiment.outreach_complete
    ? Math.max(experiment.total_leads - outreached, notContacted.length)
    : notContacted.length;
  return <div className="analysis-outreach-content">
    <div className="analysis-outreach-metrics" aria-label="Outreach totals">
      <article><span>Leads selected</span><strong>{experiment.total_leads.toLocaleString()}</strong><small>Accepted into the experiment</small></article>
      <article><span>Outreached</span><strong>{outreached.toLocaleString()}</strong><small>Opening message delivered or accepted</small></article>
      <article><span>Not contacted</span><strong>{unavailableCount.toLocaleString()}</strong><small>No opening message was sent</small></article>
      <article><span>Conversations</span><strong>{experiment.outreach_conversations.toLocaleString()}</strong><small>Distinct owner threads created</small></article>
    </div>
    <section className="analysis-not-contacted">
      <div><span>NOT CONTACTED</span><h4>{unavailableCount ? `${unavailableCount} selected ${unavailableCount === 1 ? "lead was" : "leads were"} not reached` : "Every selected lead was reached"}</h4><p>{unavailableCount ? "These leads were stopped before a valid opening message could be sent." : "There are no outreach exceptions for this experiment."}</p></div>
      {notContacted.length ? <div className="analysis-not-contacted-list">{notContacted.map((lead) => <article key={`${lead.lead_id}-${lead.variant}`}>
        <div><strong>{lead.owner_name || `Lead #${lead.lead_id}`}</strong><span>{lead.property_address || "No property address"}</span><small>{lead.variant}</small></div>
        <p><span>Reason</span>{lead.reason}</p>
      </article>)}</div> : unavailableCount ? <p className="analysis-outreach-state">The total is available, but this older outreach does not have a per-lead reason recorded.</p> : null}
    </section>
  </div>;
}
