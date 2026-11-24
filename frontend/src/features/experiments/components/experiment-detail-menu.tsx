"use client";

import { useEffect, useRef, useState } from "react";

import { renameExperiment } from "../api/experiments-api";
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
  </>;
}
