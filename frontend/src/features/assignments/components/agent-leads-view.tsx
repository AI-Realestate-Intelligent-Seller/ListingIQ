"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RetrievalProgress } from "@/components/loading/retrieval-progress";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { listMyAssignedLeads, updateMyLeadStage } from "../api/assignments-api";
import type { AssignmentLead, AssignmentStage } from "../types/assignments.types";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";

const STAGES: { value: AssignmentStage; label: string }[] = [
  { value: "new", label: "New" },
  { value: "processing", label: "In progress" },
  { value: "want_more_info", label: "Wants info" },
  { value: "interested", label: "Interested" },
  { value: "ready_to_sell", label: "Ready to sell" },
  { value: "not_interested", label: "Not interested" },
  { value: "no_response", label: "No response" },
  { value: "dnc", label: "Do not contact" },
];

function activity(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

export function AgentLeadsView({ focusLeadId = null }: { focusLeadId?: number | null }) {
  const focusedRowRef = useRef<HTMLTableRowElement | null>(null);
  const [leads, setLeads] = useState<AssignmentLead[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [openStageId, setOpenStageId] = useState<number | null>(null);
  const [stageMenuPosition, setStageMenuPosition] = useState({ top: 0, left: 0 });
  const [error, setError] = useState("");
  const stageMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (isLoading || focusLeadId === null) return;
    focusedRowRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    focusedRowRef.current?.focus({ preventScroll: true });
  }, [isLoading, focusLeadId, leads]);

  useEffect(() => {
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    listMyAssignedLeads(session.access_token, controller.signal)
      .then((response) => setLeads(response.leads))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "We could not load your assigned leads.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (openStageId === null) return;
    const closeMenu = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (!stageMenuRef.current?.contains(target) && !target.closest("[data-stage-trigger]")) setOpenStageId(null);
    };
    const closeOnViewportChange = () => setOpenStageId(null);
    document.addEventListener("pointerdown", closeMenu);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openStageId]);

  const campaigns = useMemo(() => {
    const groups = new Map<string, AssignmentLead[]>();
    for (const lead of leads) {
      const key = lead.campaign_name || "Uncategorized";
      groups.set(key, [...(groups.get(key) ?? []), lead]);
    }
    return [...groups.entries()];
  }, [leads]);

  async function changeStage(lead: AssignmentLead, stage: AssignmentStage) {
    setOpenStageId(null);
    const session = readAuthSession();
    if (!session || stage === lead.assignment_stage) return;
    setSavingId(lead.id);
    setError("");
    try {
      const saved = await updateMyLeadStage(lead.id, stage, session.access_token);
      setLeads((current) => current.map((item) => item.id === lead.id ? saved : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not update the lead stage.");
    } finally {
      setSavingId(null);
    }
  }

  return (
    <section className="assignments-view agent-leads-view">
      <header className="assignments-header">
        <div><span>MY WORK</span><h1 className="view-title">Leads</h1><p>Assigned leads organized by campaign.</p></div>
        <div className="dashboard-header-actions">
          <NotificationBell />
          <strong>{leads.length} assigned {leads.length === 1 ? "lead" : "leads"}</strong>
        </div>
      </header>
      {campaigns.map(([campaign, rows]) => (
        <section className="agent-campaign-group" key={campaign}>
          <div className="agent-campaign-heading"><div><span>CAMPAIGN</span><h2>{campaign}</h2></div><strong>{rows.length}</strong></div>
          <div className="leads-table-wrap assignments-table-wrap">
            <table className="leads-table agent-leads-table">
              <thead><tr><th>Owner / Property</th><th>Signals</th><th>Stage</th><th>Last activity</th><th>Phone</th></tr></thead>
              <tbody>{rows.map((lead) => (
                <tr key={lead.id}
                  ref={lead.id === focusLeadId ? focusedRowRef : undefined}
                  tabIndex={lead.id === focusLeadId ? -1 : undefined}
                  style={lead.id === focusLeadId ? { outline: "2px solid var(--accent)", outlineOffset: "-2px" } : undefined}>
                  <td><strong>{lead.owner_name || lead.phone || "Unnamed owner"}</strong><span className="leads-address">{lead.property_address || "No address on file"}{lead.area ? ` · ${lead.area}` : ""}</span></td>
                  <td><span className="leads-signals">{lead.signals.length ? lead.signals.map((signal) => <span key={signal.key} className="leads-signal">{signal.label}</span>) : <span className="leads-none">—</span>}</span></td>
                  <td><button
                    type="button"
                    className={`agent-stage-select stage-${lead.assignment_stage}`}
                    data-stage-trigger
                    aria-haspopup="listbox"
                    aria-expanded={openStageId === lead.id}
                    disabled={savingId === lead.id}
                    onClick={(event) => {
                      if (openStageId === lead.id) return setOpenStageId(null);
                      const rect = event.currentTarget.getBoundingClientRect();
                      const menuWidth = Math.max(rect.width, 190);
                      setStageMenuPosition({ top: rect.bottom + 6, left: Math.max(10, Math.min(rect.left, window.innerWidth - menuWidth - 10)) });
                      setOpenStageId(lead.id);
                    }}
                  >
                    {STAGES.find((stage) => stage.value === lead.assignment_stage)?.label}
                    <span className="agent-stage-chevron" aria-hidden="true" />
                  </button></td>
                  <td className="leads-activity">{activity(lead.last_activity_at)}</td>
                  <td>{lead.phone ? <a href={`tel:${lead.phone}`}>{lead.phone}</a> : "—"}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </section>
      ))}
      {isLoading ? (
        <RetrievalProgress
          eyebrow="ASSIGNMENTS"
          title="Retrieving assignments"
          description="Preparing the leads assigned to you."
          status="Fetching assignments for you"
          detail="Loading campaigns, lead details, and current stages"
          ariaLabel="Loading assignments"
        />
      ) : null}
      {!isLoading && !error && focusLeadId !== null && !leads.some((lead) => lead.id === focusLeadId) ? (
        <p role="status">This lead is no longer assigned to you.</p>
      ) : null}
      {!isLoading && leads.length === 0 ? <div className="leads-empty agent-leads-empty"><div>◎</div><h3>No leads assigned yet</h3><p>Leads will appear here after your Area Broker assigns them to you.</p></div> : null}
      {error ? <p className="sms-toast error" role="alert">{error}</p> : null}
      {openStageId !== null ? createPortal((() => {
        const lead = leads.find((item) => item.id === openStageId);
        if (!lead) return null;
        return <div ref={stageMenuRef} className="agent-stage-menu followups-status-menu" role="listbox" aria-label="Lead status" style={stageMenuPosition}>
          {STAGES.map((stage) => <button key={stage.value} type="button" role="option" aria-selected={lead.assignment_stage === stage.value} className={lead.assignment_stage === stage.value ? "active" : ""} onClick={() => void changeStage(lead, stage.value)}><span>{stage.label}</span>{lead.assignment_stage === stage.value ? <span aria-hidden="true">✓</span> : null}</button>)}
        </div>;
      })(), document.body) : null}
    </section>
  );
}
