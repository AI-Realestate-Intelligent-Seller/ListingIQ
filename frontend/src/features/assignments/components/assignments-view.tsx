"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { assignLead, listAssignments, roundRobinAssignments } from "../api/assignments-api";
import type { AssignmentLead, AssignmentsResponse } from "../types/assignments.types";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";

function activity(value: string | null): string {
  if (!value) return "—";
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
}

export function AssignmentsView() {
  const [data, setData] = useState<AssignmentsResponse>({ leads: [], agents: [] });
  const [isLoading, setIsLoading] = useState(true);
  const [savingId, setSavingId] = useState<number | null>(null);
  const [openAssigneeId, setOpenAssigneeId] = useState<number | null>(null);
  const [assigneeMenuPosition, setAssigneeMenuPosition] = useState({ top: 0, left: 0, width: 180 });
  const [isAutoAssigning, setIsAutoAssigning] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const assigneeMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    listAssignments(session.access_token, controller.signal)
      .then(setData)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "We could not load replied leads.");
      })
      .finally(() => setIsLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (openAssigneeId === null) return;
    const closeMenu = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (!assigneeMenuRef.current?.contains(target) && !target.closest("[data-assignee-trigger]")) {
        setOpenAssigneeId(null);
      }
    };
    const closeOnViewportChange = () => setOpenAssigneeId(null);
    document.addEventListener("pointerdown", closeMenu);
    window.addEventListener("resize", closeOnViewportChange);
    window.addEventListener("scroll", closeOnViewportChange, true);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      window.removeEventListener("resize", closeOnViewportChange);
      window.removeEventListener("scroll", closeOnViewportChange, true);
    };
  }, [openAssigneeId]);

  const campaigns = useMemo(() => {
    const groups = new Map<string, { name: string; leads: AssignmentLead[] }>();
    for (const lead of data.leads) {
      const key = lead.campaign_id === null ? "uncategorized" : String(lead.campaign_id);
      const group = groups.get(key) ?? { name: lead.campaign_name || "Uncategorized", leads: [] };
      groups.set(key, { ...group, leads: [...group.leads, lead] });
    }
    return [...groups.entries()];
  }, [data.leads]);

  async function updateAssignee(lead: AssignmentLead, rawValue: string) {
    setOpenAssigneeId(null);
    const session = readAuthSession();
    if (!session) return;
    const previous = lead.assignee_id;
    const agentId = rawValue ? Number(rawValue) : null;
    setError("");
    setSavingId(lead.id);
    setData((current) => ({ ...current, leads: current.leads.map((row) => row.id === lead.id ? { ...row, assignee_id: agentId } : row) }));
    try {
      const saved = await assignLead(lead.id, agentId, session.access_token);
      setData((current) => ({ ...current, leads: current.leads.map((row) => row.id === lead.id ? saved : row) }));
    } catch (reason) {
      setData((current) => ({ ...current, leads: current.leads.map((row) => row.id === lead.id ? { ...row, assignee_id: previous } : row) }));
      setError(reason instanceof Error ? reason.message : "We could not save the assignment.");
    } finally {
      setSavingId(null);
    }
  }

  async function autoAssign() {
    const session = readAuthSession();
    if (!session || isAutoAssigning) return;
    setError("");
    setNotice("");
    setIsAutoAssigning(true);
    try {
      const result = await roundRobinAssignments(session.access_token);
      setData({ leads: result.leads, agents: result.agents });
      setNotice(result.assigned
        ? `${result.assigned} ${result.assigned === 1 ? "lead was" : "leads were"} assigned automatically.`
        : "Every replied lead is already assigned.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not assign the leads automatically.");
    } finally {
      setIsAutoAssigning(false);
    }
  }

  return (
    <section className="assignments-view">
      <header className="assignments-header">
        <div><span>TEAM WORKFLOW</span><h1 className="view-title">Assignments</h1><p>Assign replied leads to agents linked to you.</p></div>
        <div className="assignments-header-actions">
          <NotificationBell />
          <strong>{data.leads.length} replied {data.leads.length === 1 ? "lead" : "leads"}</strong>
          <button className="button" type="button" disabled={isLoading || isAutoAssigning || data.agents.length === 0 || !data.leads.some((lead) => lead.assignee_id === null)} onClick={() => void autoAssign()}>
            {isAutoAssigning ? "Assigning…" : "Round Robin · Automatic Assignment"}
          </button>
        </div>
      </header>
      {campaigns.map(([campaignId, campaign]) => (
      <section className="broker-assignment-campaign" key={campaignId}>
        <div className="agent-campaign-heading">
          <div><span>CAMPAIGN</span><h2>{campaign.name}</h2></div>
          <strong>{campaign.leads.length}</strong>
        </div>
        <div className="leads-table-wrap assignments-table-wrap">
        <table className="leads-table assignments-table">
          <thead><tr><th>Owner / Property</th><th>Signals</th><th className="leads-numeric">Score</th><th>Stage</th><th>Last activity</th><th>Phone</th><th>Assignee</th></tr></thead>
          <tbody>{campaign.leads.map((lead) => (
            <tr key={lead.id}>
              <td><strong>{lead.owner_name || lead.phone || "Unnamed owner"}</strong><span className="leads-address">{lead.property_address || "No address on file"}{lead.area ? ` · ${lead.area}` : ""}</span></td>
              <td><span className="leads-signals">{lead.signals.length ? lead.signals.map((signal) => <span key={signal.key} className="leads-signal">{signal.label}</span>) : <span className="leads-none">—</span>}</span></td>
              <td className="leads-numeric"><span className="leads-score">{lead.score}</span></td>
              <td><span className="leads-stage campaign">Replied</span></td>
              <td className="leads-activity">{activity(lead.last_activity_at)}</td>
              <td>{lead.phone ? <a href={`tel:${lead.phone}`}>{lead.phone}</a> : "—"}</td>
              <td><button
                type="button"
                className="assignment-assignee-trigger"
                data-assignee-trigger
                aria-label={`Assignee for ${lead.owner_name || "lead"}`}
                aria-haspopup="listbox"
                aria-expanded={openAssigneeId === lead.id}
                disabled={savingId === lead.id || data.agents.length === 0}
                onClick={(event) => {
                  if (openAssigneeId === lead.id) return setOpenAssigneeId(null);
                  const rect = event.currentTarget.getBoundingClientRect();
                  const width = Math.max(rect.width, 180);
                  setAssigneeMenuPosition({
                    top: rect.bottom + 6,
                    left: Math.max(10, Math.min(rect.left, window.innerWidth - width - 10)),
                    width,
                  });
                  setOpenAssigneeId(lead.id);
                }}
              >
                <span>{data.agents.find((agent) => agent.id === lead.assignee_id)?.full_name || data.agents.find((agent) => agent.id === lead.assignee_id)?.email || "Unassigned"}</span>
                <span className="agent-stage-chevron" aria-hidden="true" />
              </button></td>
            </tr>
          ))}</tbody>
        </table>
        </div>
      </section>
      ))}
      {isLoading ? <p className="sms-muted">Loading replied leads…</p> : null}
      {!isLoading && data.leads.length === 0 ? <div className="leads-empty broker-assignments-empty"><div>◎</div><h3>No replied leads yet</h3><p>Leads will appear here after an owner replies to a campaign sent by you.</p></div> : null}
      {!isLoading && data.agents.length === 0 ? <p className="assignments-note">No agents are linked to you yet. Ask your HOB to assign an agent to you.</p> : null}
      {notice ? <p className="sms-toast" role="status">{notice}</p> : null}
      {error ? <p className="sms-toast error" role="alert">{error}</p> : null}
      {openAssigneeId !== null ? createPortal((() => {
        const lead = data.leads.find((item) => item.id === openAssigneeId);
        if (!lead) return null;
        return <div ref={assigneeMenuRef} className="assignment-assignee-menu" role="listbox" aria-label={`Assignee for ${lead.owner_name || "lead"}`} style={assigneeMenuPosition}>
          <button type="button" role="option" aria-selected={lead.assignee_id === null} className={lead.assignee_id === null ? "active" : ""} onClick={() => void updateAssignee(lead, "")}>Unassigned</button>
          {data.agents.map((agent) => <button key={agent.id} type="button" role="option" aria-selected={lead.assignee_id === agent.id} className={lead.assignee_id === agent.id ? "active" : ""} onClick={() => void updateAssignee(lead, String(agent.id))}>{agent.full_name || agent.email}</button>)}
        </div>;
      })(), document.body) : null}
    </section>
  );
}
