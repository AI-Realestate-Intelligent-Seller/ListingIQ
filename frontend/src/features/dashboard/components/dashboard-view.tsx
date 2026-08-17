"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/brand/brand";
import {
  clearAuthSession,
  readAuthSession,
} from "@/features/auth/lib/auth-storage";
import { endSession, markActivity, watchSession } from "@/features/auth/lib/session-guard";
import type { AuthUser, InvitableRole } from "@/features/auth/types/auth.types";
import { createInvitation } from "@/features/auth/api/auth-api";
import { FollowUpsBoard } from "@/features/followups/components/followups-board";
import { LeadPool } from "@/features/leads/components/lead-pool";
import { SmsWorkspace } from "@/features/sms/components/sms-workspace";

type DashboardTab = "overview" | "leads" | "sms" | "followups";

const METRICS = [
  { label: "Seller opportunities", value: "24", change: "+12% this week" },
  { label: "Conversations active", value: "11", change: "4 need attention" },
  { label: "Agent handoffs", value: "7", change: "+2 this week" },
];

const ROLE_LABELS: Record<InvitableRole, string> = {
  broker: "Area Broker",
  agent: "Agent",
};

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
  /** Mobile only: the sidebar is an off-canvas drawer behind the ☰ toggle. */
  const [isNavOpen, setIsNavOpen] = useState(false);
  /** Set when the Lead Pool sends the broker to a particular SMS thread. */
  const [focusConversationId, setFocusConversationId] = useState<number | null>(null);

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

    setIsInviting(true);
    try {
      const response = await createInvitation(email, role, session.access_token);
      setInviteSuccess(
        `${response.message} ${email} was invited as ${ROLE_LABELS[role]} and has 48 hours to accept.`,
      );
      formElement.reset();
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
        <nav aria-label="Dashboard navigation">
          <button
            type="button"
            className={view === "overview" ? "active" : undefined}
            onClick={() => openTab("overview")}
          >
            Overview
          </button>
          <button
            type="button"
            className={view === "leads" ? "active" : undefined}
            onClick={() => openTab("leads")}
          >
            Lead Pool
          </button>
          <button
            type="button"
            className={view === "sms" ? "active" : undefined}
            onClick={() => openTab("sms")}
          >
            SMS
          </button>
          <button
            type="button"
            className={view === "followups" ? "active" : undefined}
            onClick={() => openTab("followups")}
          >
            Follow-ups
          </button>
        </nav>
        <button type="button" onClick={logout}>Log out</button>
      </aside>

      <section className="dashboard-main" id="overview">
        {/* The greeting belongs to Overview. Lead Pool and SMS are working
            screens that carry their own headings and need the vertical space. */}
        {view === "overview" ? (
          <header className="dashboard-header">
            <div>
              <span>{user.role.toUpperCase()} WORKSPACE</span>
              <h1>Good to see you, {user.first_name}.</h1>
              <p>{user.brokerage_name}</p>
            </div>
            <div className="dashboard-avatar" aria-label={user.full_name}>
              {user.first_name.charAt(0)}{user.last_name.charAt(0)}
            </div>
          </header>
        ) : null}

        {view === "leads" ? (
          <LeadPool
            onCampaignStarted={() => setView("sms")}
            onOpenConversation={(conversationId) => {
              setFocusConversationId(conversationId);
              setView("sms");
            }}
          />
        ) : null}

        {view === "sms" ? <SmsWorkspace focusConversationId={focusConversationId} /> : null}

        {view === "followups" ? (
          <FollowUpsBoard focusConversationId={focusConversationId} />
        ) : null}

        {view === "overview" ? (
        <>
        <div className="dashboard-metrics">
          {METRICS.map((metric) => (
            <article key={metric.label}>
              <span>{metric.label}</span>
              <strong>{metric.value}</strong>
              <small>{metric.change}</small>
            </article>
          ))}
        </div>

        {user.role === "hob" ? (
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
              <select name="role" aria-label="Role" defaultValue="agent" disabled={isInviting}>
                <option value="broker">Area Broker</option>
                <option value="agent">Agent</option>
              </select>
              <button className="button" type="submit" disabled={isInviting}>
                {isInviting ? "Sending…" : "Send Invitation"}
              </button>
            </form>
            {inviteError ? (
              <p className="invite-error" role="alert">{inviteError}</p>
            ) : null}
            {inviteSuccess ? (
              <div className="invite-result" role="status">
                <strong>✓ Invitation sent successfully</strong>
                <p>{inviteSuccess}</p>
              </div>
            ) : null}
          </section>
        ) : null}

        <section className="dashboard-panel">
          <div>
            <span>RECENT ACTIVITY</span>
            <h2>{user.role === "agent" ? "Your assigned sellers" : "Your seller pipeline"}</h2>
          </div>
          <div className="dashboard-empty">
            <div>◎</div>
            <h3>Your dashboard is ready</h3>
            <p>Seller opportunities and conversations will appear here as your workspace grows.</p>
            <button className="button" type="button" onClick={() => setView("sms")}>
              Open SMS workspace
            </button>
          </div>
        </section>
        </>
        ) : null}
      </section>
    </main>
  );
}
