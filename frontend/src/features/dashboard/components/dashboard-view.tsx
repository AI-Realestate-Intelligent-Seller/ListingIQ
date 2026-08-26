"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Brand } from "@/components/brand/brand";
import {
  clearAuthSession,
  readAuthSession,
} from "@/features/auth/lib/auth-storage";
import { endSession, markActivity, watchSession } from "@/features/auth/lib/session-guard";
import type { AuthUser, BrokerageOverview, DirectoryMember, InvitableRole } from "@/features/auth/types/auth.types";
import { createInvitation, getBrokerageOverview, getTeamDirectory } from "@/features/auth/api/auth-api";
import { CampaignsView } from "@/features/campaigns/components/campaigns-view";
import { FollowUpsBoard } from "@/features/followups/components/followups-board";
import { LeadPool } from "@/features/leads/components/lead-pool";
import { AssignmentsView } from "@/features/assignments/components/assignments-view";
import { AgentLeadsView } from "@/features/assignments/components/agent-leads-view";
import { getAgentOverview, getBrokerOverview, getMyBroker } from "@/features/assignments/api/assignments-api";
import type { AgentOverview, BrokerOverview } from "@/features/assignments/types/assignments.types";

type DashboardTab = "overview" | "directory" | "assignments" | "leads" | "campaigns" | "followups";

type InviteDropdownProps = {
  name: string;
  label: string;
  value: string;
  placeholder?: string;
  options: { value: string; label: string }[];
  disabled?: boolean;
  required?: boolean;
  onChange: (value: string) => void;
};

function InviteDropdown({ name, label, value, placeholder = "Select", options,
  disabled = false, required = false, onChange }: InviteDropdownProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const selected = options.find((option) => option.value === value);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);

  return (
    <div className={`invite-dropdown${open ? " open" : ""}`} ref={rootRef}>
      <input type="hidden" name={name} value={value} />
      <button
        type="button"
        className="invite-dropdown-trigger"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-required={required}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={selected ? "" : "placeholder"}>{selected?.label ?? placeholder}</span>
        <span aria-hidden="true">⌄</span>
      </button>
      {open ? (
        <div className="invite-dropdown-menu" role="listbox" aria-label={label}>
          {options.map((option) => (
            <button
              key={option.value}
              type="button"
              role="option"
              aria-selected={option.value === value}
              className={option.value === value ? "active" : ""}
              onClick={() => { onChange(option.value); setOpen(false); }}
            >
              <span>{option.label}</span>
              {option.value === value ? <span aria-hidden="true">✓</span> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatResponseTime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${Math.round(seconds)} sec`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} hr`;
}

function assignmentDonut(overview: AgentOverview): string {
  if (!overview.assigned_leads) return "conic-gradient(#e5e7e2 0 100%)";
  const newEnd = (overview.new_assignments / overview.assigned_leads) * 100;
  const progressEnd = newEnd + (overview.in_progress_leads / overview.assigned_leads) * 100;
  return `conic-gradient(#b8863b 0 ${newEnd}%, #5c9789 ${newEnd}% ${progressEnd}%, var(--blue) ${progressEnd}% 100%)`;
}

const ROLE_LABELS: Record<InvitableRole, string> = {
  broker: "Area Broker",
  agent: "Agent",
};

function TeamDirectory() {
  const [members, setMembers] = useState<DirectoryMember[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    const session = readAuthSession();
    if (!session) return;

    getTeamDirectory(session.access_token, controller.signal)
      .then((response) => setMembers(response.members))
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "We could not load your directory.");
      })
      .finally(() => setIsLoading(false));

    return () => controller.abort();
  }, []);

  return (
    <section className="dashboard-panel directory-panel" aria-labelledby="directory-title">
      <div className="directory-heading">
        <div>
          <span>TEAM MANAGEMENT</span>
          <h1 className="view-title" id="directory-title">Team Management</h1>
          <p>Agents and area brokers who have joined your brokerage.</p>
        </div>
        {!isLoading && !error ? <strong>{members.length} {members.length === 1 ? "member" : "members"}</strong> : null}
      </div>

      {isLoading ? <div className="directory-state" role="status">Loading team directory…</div> : null}
      {error ? <div className="directory-state directory-error" role="alert">{error}</div> : null}
      {!isLoading && !error && members.length === 0 ? (
        <div className="directory-state">
          <h2>No team members yet</h2>
          <p>Accepted agent and area broker invitations will appear here.</p>
        </div>
      ) : null}
      {!isLoading && !error && members.length > 0 ? (
        <div className="directory-table-wrap">
          <table className="directory-table">
            <thead><tr><th>Name</th><th>Role</th><th>Email</th></tr></thead>
            <tbody>
              {members.map((member) => (
                <tr key={member.id}>
                  <td><strong>{member.full_name || "Unnamed member"}</strong></td>
                  <td><span className={`directory-role directory-role-${member.role}`}>{ROLE_LABELS[member.role]}</span></td>
                  <td><a href={`mailto:${member.email}`}>{member.email}</a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

/**
 * Shared by /dashboard and /dashboard/[role]. Both routes render this component
 * rather than re-exporting one page from the other: Turbopack cannot resolve a
 * client component's default export through a route-to-route re-export, which
 * fails at runtime with "Could not find the module … in the React Client Manifest".
 */
export function DashboardView() {
  const router = useRouter();
  const params = useParams<{ role?: string }>();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [view, setView] = useState<DashboardTab>("overview");
  const [isInviting, setIsInviting] = useState(false);
  const [inviteSuccess, setInviteSuccess] = useState("");
  const [inviteError, setInviteError] = useState("");
  const [inviteRole, setInviteRole] = useState<InvitableRole>("agent");
  const [inviteBrokerId, setInviteBrokerId] = useState("");
  const [inviteBrokers, setInviteBrokers] = useState<DirectoryMember[]>([]);
  const [agentBrokerName, setAgentBrokerName] = useState("");
  const [isLoadingBrokers, setIsLoadingBrokers] = useState(false);
  /** Mobile only: the sidebar is an off-canvas drawer behind the ☰ toggle. */
  const [isNavOpen, setIsNavOpen] = useState(false);
  /** Set when the Lead Pool sends the broker to a particular SMS thread. */
  const [focusConversationId, setFocusConversationId] = useState<number | null>(null);
  /** The draft the Lead Pool just created, waiting to be composed in Campaigns. */
  const [draftCampaignId, setDraftCampaignId] = useState<number | null>(null);
  /** Set when Campaigns sends the broker to one campaign's replies. */
  const [followUpCampaignId, setFollowUpCampaignId] = useState<number | null>(null);
  const [hobOverview, setHobOverview] = useState<BrokerageOverview | null>(null);
  const [hobOverviewError, setHobOverviewError] = useState("");
  const [agentOverview, setAgentOverview] = useState<AgentOverview | null>(null);
  const [agentOverviewError, setAgentOverviewError] = useState("");
  const [brokerOverview, setBrokerOverview] = useState<BrokerOverview | null>(null);
  const [brokerOverviewError, setBrokerOverviewError] = useState("");

  useEffect(() => {
    const session = readAuthSession();
    if (!session) {
      router.replace("/login");
      return;
    }
    if (params.role && params.role !== session.user.role) {
      router.replace(`/dashboard/${session.user.role}`);
      return;
    }
    markActivity();
    // Browser storage is an external system and is only available after mount.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setUser(session.user);
  }, [params.role, router]);

  useEffect(() => {
    if (user?.role !== "hob") return;
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    setIsLoadingBrokers(true);
    getTeamDirectory(session.access_token, controller.signal)
      .then((response) => setInviteBrokers(response.members.filter((member) => member.role === "broker")))
      .catch(() => setInviteBrokers([]))
      .finally(() => setIsLoadingBrokers(false));
    return () => controller.abort();
  }, [user?.role]);

  useEffect(() => {
    if (user?.role !== "broker") return;
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    getBrokerOverview(session.access_token, controller.signal)
      .then((response) => {
        setBrokerOverview(response);
        setBrokerOverviewError("");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setBrokerOverviewError(reason instanceof Error ? reason.message : "Could not load your brokerage metrics.");
      });
    return () => controller.abort();
  }, [user?.role]);

  useEffect(() => {
    if (user?.role !== "hob") return;
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    getBrokerageOverview(session.access_token, controller.signal)
      .then((response) => {
        setHobOverview(response);
        setHobOverviewError("");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setHobOverviewError(reason instanceof Error ? reason.message : "Could not load brokerage analytics.");
      });
    return () => controller.abort();
  }, [user?.role]);

  useEffect(() => {
    if (user?.role !== "agent") return;
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    getMyBroker(session.access_token, controller.signal)
      .then((response) => setAgentBrokerName(response.broker?.full_name || response.broker?.email || ""))
      .catch(() => setAgentBrokerName(""));
    return () => controller.abort();
  }, [user?.role]);

  useEffect(() => {
    if (user?.role !== "agent") return;
    const session = readAuthSession();
    if (!session) return;
    const controller = new AbortController();
    getAgentOverview(session.access_token, controller.signal)
      .then((response) => {
        setAgentOverview(response);
        setAgentOverviewError("");
      })
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setAgentOverviewError(reason instanceof Error ? reason.message : "Could not load your assignment analytics.");
      });
    return () => controller.abort();
  }, [user?.role]);

  // End the session after 8 hours of inactivity, refreshing the access token
  // silently for as long as the user keeps working.
  useEffect(() => {
    return watchSession(() => {
      endSession();
      router.replace("/login?expired=1");
    });
  }, [router]);

  async function inviteTeamMember(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isInviting) return;

    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const email = String(form.get("email") ?? "").trim();
    const role: InvitableRole = form.get("role") === "broker" ? "broker" : "agent";
    const brokerIdValue = String(form.get("brokerId") ?? "");
    const brokerId = role === "agent" ? Number(brokerIdValue) : undefined;

    setInviteError("");
    setInviteSuccess("");

    const session = readAuthSession();
    if (!session) {
      router.replace("/login");
      return;
    }

    const brokerageDomain = session.user.email.split("@")[1] ?? "";
    if (!email.toLowerCase().endsWith(`@${brokerageDomain.toLowerCase()}`)) {
      setInviteError(`Team members must use your brokerage domain (@${brokerageDomain}).`);
      return;
    }
    if (role === "agent" && (!brokerIdValue || !Number.isInteger(brokerId))) {
      setInviteError("Select an Area Broker for this agent.");
      return;
    }

    setIsInviting(true);
    try {
      const response = await createInvitation(email, role, session.access_token, brokerId);
      setInviteSuccess(
        `${response.message} ${email} was invited as ${ROLE_LABELS[role]} and has 48 hours to accept.`,
      );
      formElement.reset();
      setInviteBrokerId("");
    } catch (error) {
      setInviteError(
        error instanceof Error
          ? error.message
          : "We could not send the invitation. Please try again.",
      );
    } finally {
      setIsInviting(false);
    }
  }

  // Escape closes the mobile navigation drawer.
  useEffect(() => {
    if (!isNavOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isNavOpen]);

  /** Choosing a tab closes the drawer so the phone lands on the content. */
  function openTab(tab: DashboardTab): void {
    setView(tab);
    setIsNavOpen(false);
  }

  /**
   * Follow-ups is the primary conversation workspace. The legacy SMS tab stays
   * available, but no cross-page action depends on it.
   */
  function openConversation(conversationId: number): void {
    setFocusConversationId(conversationId);
    setView("followups");
  }

  function logout(): void {
    clearAuthSession();
    router.replace("/login");
  }

  if (!user) return null;

  const brokerageDomain = user.email.split("@")[1];

  return (
    <main className={`dashboard-shell${isNavOpen ? " nav-open" : ""}`}>
      {/* Phone only: a bar that holds the drawer toggle above the content. */}
      <header className="dashboard-topbar">
        <button
          type="button"
          className="dashboard-nav-toggle"
          onClick={() => setIsNavOpen(true)}
          aria-label="Open navigation"
          aria-expanded={isNavOpen}
          aria-controls="dashboard-nav"
        >
          ☰
        </button>
        <Brand />
      </header>
      <div
        className="dashboard-nav-scrim"
        role="presentation"
        onClick={() => setIsNavOpen(false)}
      />

      <aside className="dashboard-sidebar" id="dashboard-nav">
        <Brand />
        <button
          type="button"
          className="dashboard-nav-close"
          onClick={() => setIsNavOpen(false)}
          aria-label="Close navigation"
        >
          ✕
        </button>
        <nav className="dashboard-nav-list" role="tablist" aria-label="Dashboard sections">
          <button
            type="button"
            role="tab"
            aria-selected={view === "overview"}
            className={view === "overview" ? "active" : undefined}
            onClick={() => openTab("overview")}
          >
            <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
              <rect x="2.5" y="2.5" width="6.5" height="6.5" rx="1.3" />
              <rect x="11" y="2.5" width="6.5" height="4.5" rx="1.3" />
              <rect x="11" y="9.5" width="6.5" height="8" rx="1.3" />
              <rect x="2.5" y="11.5" width="6.5" height="6" rx="1.3" />
            </svg>
            <span>Overview</span>
          </button>
          {user.role === "broker" ? (
            <button
              type="button"
              role="tab"
              aria-selected={view === "assignments"}
              className={view === "assignments" ? "active" : undefined}
              onClick={() => openTab("assignments")}
            >
              <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
                <path d="M3 5.5h9M3 10h7M3 14.5h5M13 12l2 2 3-4" />
              </svg>
              <span>Assignments</span>
            </button>
          ) : null}
          <button
            type="button"
            role="tab"
            aria-selected={view === "leads"}
            className={view === "leads" ? "active" : undefined}
            onClick={() => openTab("leads")}
          >
            <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M3 4h14l-5.5 6.5v5L8.5 17v-6.5L3 4z" />
            </svg>
            <span>{user.role === "agent" ? "Leads" : "Lead Pool"}</span>
          </button>
          {user.role !== "agent" ? <button
            type="button"
            role="tab"
            aria-selected={view === "campaigns"}
            className={view === "campaigns" ? "active" : undefined}
            onClick={() => openTab("campaigns")}
          >
            <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
              <rect x="2.5" y="4" width="15" height="12" rx="2" />
              <path d="M3 6l7 5 7-5" />
            </svg>
            <span>Campaigns</span>
          </button> : null}
          <button
            type="button"
            role="tab"
            aria-selected={view === "followups"}
            className={view === "followups" ? "active" : undefined}
            onClick={() => openTab("followups")}
          >
            <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M3 4.5h14v9H8l-3.5 3v-3H3v-9z" />
            </svg>
            <span>Follow-ups</span>
          </button>
          {user.role === "hob" ? (
            <button
              type="button"
              role="tab"
              aria-selected={view === "directory"}
              className={view === "directory" ? "active" : undefined}
              onClick={() => openTab("directory")}
            >
              <svg className="dashboard-nav-icon" viewBox="0 0 20 20" aria-hidden="true">
                <circle cx="7" cy="6" r="2.5" />
                <circle cx="14" cy="7" r="2" />
                <path d="M2.5 16c.4-3.2 2-4.8 4.5-4.8s4.1 1.6 4.5 4.8M11.5 12c2.8-.7 5.2.7 5.8 3.5" />
              </svg>
              <span>Team Management</span>
            </button>
          ) : null}
        </nav>
        <button type="button" onClick={logout}>Log out</button>
      </aside>

      <section className={`dashboard-main dashboard-main-${view}`} id="overview">
        {/* The greeting belongs to Overview. Lead Pool and SMS are working
            screens that carry their own headings and need the vertical space. */}
        {view === "overview" ? (
          <header className="dashboard-header">
            <div>
              <span>{user.role.toUpperCase()} WORKSPACE</span>
              <h1 className="view-title">Good to see you, {user.first_name}.</h1>
              <p>
                {user.brokerage_name}
                {user.role === "agent" ? ` · Area Broker: ${agentBrokerName || "Not assigned"}` : ""}
              </p>
            </div>
            <div className="dashboard-avatar" aria-label={user.full_name}>
              {user.first_name.charAt(0)}{user.last_name.charAt(0)}
            </div>
          </header>
        ) : null}

        {view === "leads" && user.role === "agent" ? <AgentLeadsView /> : null}

        {view === "leads" && user.role !== "agent" ? (
          <LeadPool
            onDraftCampaign={(campaignId) => {
              setDraftCampaignId(campaignId);
              setView("campaigns");
            }}
            onOpenConversation={openConversation}
          />
        ) : null}

        {view === "directory" && user.role === "hob" ? (
          <>
            <section className="dashboard-panel invite-panel">
              <div>
                <span>TEAM MANAGEMENT</span>
                <h2>Invite a broker or agent</h2>
                <p className="invite-domain-note">
                  Team members must use your brokerage domain: <strong>@{brokerageDomain}</strong>
                </p>
              </div>
              <form onSubmit={inviteTeamMember} noValidate={false}>
                <input
                  name="email"
                  type="email"
                  aria-label="Team member email"
                  placeholder={`name@${brokerageDomain}`}
                  pattern={`[^@\\s]+@${brokerageDomain.replaceAll(".", "\\.")}`}
                  title={`Use an @${brokerageDomain} email address`}
                  disabled={isInviting}
                  required
                />
                <InviteDropdown
                  name="role"
                  label="Role"
                  value={inviteRole}
                  onChange={(value) => {
                    setInviteRole(value === "broker" ? "broker" : "agent");
                    setInviteBrokerId("");
                  }}
                  disabled={isInviting}
                  options={[
                    { value: "broker", label: "Area Broker" },
                    { value: "agent", label: "Agent" },
                  ]}
                />
                {inviteRole === "agent" ? (
                  <InviteDropdown
                    name="brokerId"
                    label="Assigned Area Broker"
                    value={inviteBrokerId}
                    disabled={isInviting || isLoadingBrokers || inviteBrokers.length === 0}
                    required
                    placeholder={isLoadingBrokers ? "Loading brokers…" : inviteBrokers.length ? "Assign an Area Broker" : "Invite a broker first"}
                    options={inviteBrokers.map((broker) => ({
                      value: String(broker.id),
                      label: broker.full_name || broker.email,
                    }))}
                    onChange={setInviteBrokerId}
                  />
                ) : null}
                <button className="button" type="submit" disabled={isInviting}>
                  {isInviting ? "Sending…" : "Send Invitation"}
                </button>
              </form>
              {inviteError ? <p className="invite-error" role="alert">{inviteError}</p> : null}
              {inviteSuccess ? (
                <div className="invite-result" role="status">
                  <strong>✓ Invitation sent successfully</strong>
                  <p>{inviteSuccess}</p>
                </div>
              ) : null}
            </section>
            <TeamDirectory />
          </>
        ) : null}
        {view === "assignments" && user.role === "broker" ? <AssignmentsView /> : null}

        {view === "campaigns" ? (
          <CampaignsView
            draftId={draftCampaignId}
            onDraftHandled={() => setDraftCampaignId(null)}
            onOpenFollowUps={(campaignId) => {
              setFocusConversationId(null);
              setFollowUpCampaignId(campaignId);
              setView("followups");
            }}
            onOpenConversation={openConversation}
          />
        ) : null}

        {view === "followups" ? (
          <FollowUpsBoard
            focusConversationId={focusConversationId}
            campaignId={followUpCampaignId}
          />
        ) : null}

        {view === "overview" ? (
        <>
        <section className="dashboard-panel">
          <div>
            <span>RECENT ACTIVITY</span>
            <h2>{user.role === "agent" ? "Your assigned tasks" : "Your Listing pipeline"}</h2>
          </div>
          {user.role === "hob" && hobOverview ? (
            <div className="hob-analytics-grid">
              <article className="hob-analytics-card hob-campaign-chart">
                <div><span>CAMPAIGN PERFORMANCE</span><h3>Replies</h3></div>
                <div className="hob-donut-row">
                  <div className="hob-donut" style={{ background: `conic-gradient(var(--blue) 0 ${hobOverview.campaign_reply_rate}%, #e5e7e2 ${hobOverview.campaign_reply_rate}% 100%)` }}>
                    <div><strong>{hobOverview.campaign_reply_rate}%</strong><small>reply rate</small></div>
                  </div>
                  <dl><div><dt>Replied</dt><dd>{hobOverview.campaign_replied}</dd></div><div><dt>No reply</dt><dd>{Math.max(hobOverview.campaign_conversations - hobOverview.campaign_replied, 0)}</dd></div></dl>
                </div>
              </article>

              <article className="hob-analytics-card hob-funnel-card">
                <div><span>LEAD CONVERSION</span><h3>Pipeline funnel</h3></div>
                <div className="hob-funnel">
                  <div><label><span>Total leads</span><b>{hobOverview.total_leads}</b></label><i style={{ width: "100%" }} /></div>
                  <div><label><span>Replied</span><b>{hobOverview.campaign_replied}</b></label><i style={{ width: `${hobOverview.total_leads ? Math.max((hobOverview.campaign_replied / hobOverview.total_leads) * 100, 4) : 0}%` }} /></div>
                  <div><label><span>Appointments</span><b>{hobOverview.booked_leads}</b></label><i style={{ width: `${hobOverview.total_leads ? Math.max((hobOverview.booked_leads / hobOverview.total_leads) * 100, 4) : 0}%` }} /></div>
                </div>
                <small>{hobOverview.lead_to_appointment_rate}% lead-to-appointment rate</small>
              </article>

              <article className="hob-analytics-card hob-attention-card">
                <div><span>ATTENTION QUEUE</span><h3>Follow-up health</h3></div>
                <div className="hob-paired-metrics"><div><strong>{hobOverview.replies_requiring_attention}</strong><small>Replies need attention</small></div><div><strong>{formatResponseTime(hobOverview.average_response_seconds)}</strong><small>Average response time</small></div></div>
              </article>

              <article className="hob-analytics-card hob-monthly-card">
                <div><span>MONTHLY ACTIVITY</span><h3>This month</h3></div>
                <div className="hob-paired-metrics"><div><strong>{hobOverview.campaigns_sent_this_month}</strong><small>Campaigns sent</small></div><div><strong>{hobOverview.appointments_booked_this_month}</strong><small>Appointments booked</small></div></div>
              </article>

              <article className="hob-analytics-card hob-workload-card">
                <div><span>TEAM WORKLOAD</span><h3>Lead distribution</h3></div>
                <div className="hob-workload-list">
                  {hobOverview.workload.length ? hobOverview.workload.map((member) => {
                    const maximum = Math.max(...hobOverview.workload.map((item) => item.lead_count), 1);
                    return <div key={member.user_id} className="hob-workload-row"><label><span>{member.name}</span><small>{member.role === "broker" ? "Broker" : "Agent"}</small><b>{member.lead_count}</b></label><i><span style={{ width: `${(member.lead_count / maximum) * 100}%` }} /></i></div>;
                  }) : <p className="sms-muted">No active brokers or agents yet.</p>}
                </div>
              </article>
            </div>
          ) : null}
          {user.role === "hob" && !hobOverview && !hobOverviewError ? <p className="sms-muted">Loading brokerage analytics…</p> : null}
          {user.role === "hob" && hobOverviewError ? <p className="sms-toast error" role="alert">{hobOverviewError}</p> : null}
          {user.role === "agent" && agentOverview ? (
            <div className="agent-analytics-grid">
              <article className="agent-analytics-card agent-pipeline-card">
                <div><span>ASSIGNMENT PIPELINE</span><h3>Current workload</h3></div>
                <div className="agent-donut-row">
                  <div className="agent-donut" style={{ background: assignmentDonut(agentOverview) }}>
                    <div><strong>{agentOverview.assigned_leads}</strong><small>assigned</small></div>
                  </div>
                  <dl>
                    <div className="stage-new"><dt>New</dt><dd>{agentOverview.new_assignments}</dd></div>
                    <div className="stage-progress"><dt>In progress</dt><dd>{agentOverview.in_progress_leads}</dd></div>
                    <div className="stage-done"><dt>Completed</dt><dd>{agentOverview.completed_leads}</dd></div>
                  </dl>
                </div>
              </article>

              <article className="agent-analytics-card">
                <div><span>PERFORMANCE</span><h3>Assignment efficiency</h3></div>
                <div className="agent-paired-metrics">
                  <div><strong>{agentOverview.completion_rate}%</strong><small>Completion rate</small></div>
                  <div><strong>{formatResponseTime(agentOverview.average_handling_seconds)}</strong><small>Average handling time</small></div>
                </div>
              </article>

              <article className="agent-analytics-card">
                <div><span>FOLLOW-UP WORKLOAD</span><h3>Replies needing action</h3></div>
                <div className="agent-attention-total"><strong>{agentOverview.replies_requiring_attention}</strong><span>of {agentOverview.assigned_leads} assigned leads</span></div>
                <div className="agent-progress-track"><span style={{ width: `${agentOverview.assigned_leads ? (agentOverview.replies_requiring_attention / agentOverview.assigned_leads) * 100 : 0}%` }} /></div>
                <small>{Math.max(agentOverview.assigned_leads - agentOverview.replies_requiring_attention, 0)} leads do not currently need a reply</small>
              </article>

              <article className="agent-analytics-card">
                <div><span>RESULTS</span><h3>Appointments and completions</h3></div>
                <div className="agent-results-bars">
                  <div><label><span>Appointments booked</span><b>{agentOverview.appointments_booked}</b></label><i><span style={{ width: `${agentOverview.assigned_leads ? (agentOverview.appointments_booked / agentOverview.assigned_leads) * 100 : 0}%` }} /></i></div>
                  <div><label><span>Completed leads</span><b>{agentOverview.completed_leads}</b></label><i><span style={{ width: `${agentOverview.completion_rate}%` }} /></i></div>
                </div>
              </article>
            </div>
          ) : null}
          {user.role === "agent" && !agentOverview && !agentOverviewError ? <p className="sms-muted">Loading assignment analytics…</p> : null}
          {user.role === "agent" && agentOverviewError ? <p className="sms-toast error" role="alert">{agentOverviewError}</p> : null}
          {user.role === "broker" && brokerOverview ? (
            <div className="broker-analytics-grid">
              <article className="broker-analytics-card">
                <div><span>CAMPAIGN STATUS</span><h3>Campaign activity</h3></div>
                <div className="broker-donut-row">
                  <div className="broker-donut" style={{ background: `conic-gradient(var(--blue) 0 ${brokerOverview.campaigns_started ? (brokerOverview.campaigns_completed / brokerOverview.campaigns_started) * 100 : 0}%, var(--accent) 0 100%)` }}>
                    <div><strong>{brokerOverview.campaigns_started}</strong><small>started</small></div>
                  </div>
                  <dl><div className="campaign-completed"><dt>Completed</dt><dd>{brokerOverview.campaigns_completed}</dd></div><div className="campaign-draft"><dt>In draft</dt><dd>{brokerOverview.campaigns_in_draft}</dd></div></dl>
                </div>
              </article>

              <article className="broker-analytics-card broker-conversion-card">
                <div><span>CAMPAIGN CONVERSION</span><h3>Maturing leads</h3></div>
                <div className="broker-conversion-ring" style={{ background: `conic-gradient(var(--accent) 0 ${brokerOverview.campaign_to_matured_rate}%, #e5e7e2 ${brokerOverview.campaign_to_matured_rate}% 100%)` }}>
                  <div><strong>{brokerOverview.campaign_to_matured_rate}%</strong><small>conversion</small></div>
                </div>
                <small>Interested, ready-to-sell, or appointment-booked campaign leads</small>
              </article>

              <article className="broker-analytics-card broker-assignment-card">
                <div><span>ASSIGNMENT PIPELINE</span><h3>Agent progress</h3></div>
                <div className="broker-funnel">
                  <div><label><span>Assigned</span><b>{brokerOverview.total_assigned}</b></label><i><span style={{ width: "100%" }} /></i></div>
                  <div><label><span>In progress</span><b>{brokerOverview.total_in_progress}</b></label><i><span style={{ width: `${brokerOverview.total_assigned ? (brokerOverview.total_in_progress / brokerOverview.total_assigned) * 100 : 0}%` }} /></i></div>
                  <div><label><span>Completed</span><b>{brokerOverview.total_completed}</b></label><i><span style={{ width: `${brokerOverview.total_assigned ? (brokerOverview.total_completed / brokerOverview.total_assigned) * 100 : 0}%` }} /></i></div>
                </div>
              </article>

              <article className="broker-analytics-card">
                <div><span>RESULTS</span><h3>Completed and booked</h3></div>
                <div className="broker-paired-metrics"><div><strong>{brokerOverview.total_completed}</strong><small>Completed assignments</small></div><div><strong>{brokerOverview.total_booked}</strong><small>Appointments booked</small></div></div>
              </article>
            </div>
          ) : null}
          {user.role === "broker" && !brokerOverview && !brokerOverviewError ? <p className="sms-muted">Loading broker analytics…</p> : null}
          {user.role === "broker" && brokerOverviewError ? <p className="sms-toast error" role="alert">{brokerOverviewError}</p> : null}
        </section>
        </>
        ) : null}
      </section>
    </main>
  );
}
