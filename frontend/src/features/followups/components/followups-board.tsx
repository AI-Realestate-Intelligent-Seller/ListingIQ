"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { listCampaigns } from "@/features/campaigns/api/campaigns-api";
import type { Campaign } from "@/features/campaigns/types/campaigns.types";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession } from "@/features/auth/lib/session-guard";
import { listMessages, sendMessage, setHandover } from "@/features/sms/api/sms-api";
import {
  LEAD_LABELS,
  LEAD_STATUS_ORDER,
  dateLabel,
  formatClock,
  formatTime,
  initials,
} from "@/features/sms/lib/sms-format";
import type { LeadStatus, SmsMessage } from "@/features/sms/types/sms.types";
import { ApiRequestError } from "@/lib/api/http-client";
import { LeadDetailDrawer } from "@/features/leads/components/lead-detail-drawer";
import { LeadTimeline } from "@/features/leads/components/lead-timeline";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";

import { bookAppointment, listFollowUps, setFollowUpStatus, suggestReplies } from "../api/followups-api";
import type {
  AppointmentPayload,
  FollowUp,
  FollowUpState,
  ReplySuggestions,
} from "../types/followups.types";
import { AppointmentDialog } from "./appointment-dialog";
import {
  useNotifications,
} from "../../dashboard/components/notification-provider";
/** How often the list and the open thread refresh. */
const POLL_INTERVAL_MS = 8000;

const ROLE_LABELS: Record<string, string> = {
  hob: "Head of brokerage",
  broker: "Area broker",
  agent: "Agent",
};

const STATE_LABELS: Record<FollowUpState, string> = {
  pending: "Awaiting your decision",
  accepted: "Accepted",
  declined: "Declined",
};

/** Carrier wording for the delivery line under an outbound bubble. */
const DELIVERY_LABELS: Record<string, string> = {
  queued: "Queued",
  sending: "Sending",
  sent: "Sent",
  delivered: "Sent",
  delivery_failed: "Sent",
  sending_failed: "Sent",
  received: "Received",
};

function deliveryLabel(status: string): string {
  return DELIVERY_LABELS[status] ?? status.replaceAll("_", " ");
}

/** The channel line above a bubble, as on the prototype: who wrote it, and how. */
function channelLabel(message: SmsMessage): string {
  if (message.event_type === "broker.message") return "You · SMS";
  if (message.event_type === "broker.booking") return "You · booking";
  if (message.event_type === "calendar.confirmation") return "AI · booking";
  return "AI · SMS";
}

type FollowUpsBoardProps = {
  /** Opens a particular thread on arrival, e.g. from the Lead Pool. */
  focusConversationId?: number | null;
  /** Narrows the board to one campaign's replies, e.g. from the Campaigns tab. */
  campaignId?: number | null;
};

export function FollowUpsBoard({
  focusConversationId = null,
  campaignId = null,
}: FollowUpsBoardProps) {
  const [followUps, setFollowUps] = useState<FollowUp[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  /** Every campaign the brokerage has, so one with no replies yet is still
      offered — the list cannot be built from the replies it is meant to find. */
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [selectedCampaignId, setSelectedCampaignId] = useState(
    campaignId === null ? "" : String(campaignId),
  );
  const [isCampaignMenuOpen, setIsCampaignMenuOpen] = useState(false);
  const [isStatusMenuOpen, setIsStatusMenuOpen] = useState(false);
  const [scope, setScope] = useState<"replied" | "all">(
    campaignId === null ? "replied" : "all",
  );
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isWorking, setIsWorking] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSchedulerOpen, setIsSchedulerOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  /** Drafted replies, tagged with the thread whose history they answer. */
  const [drafts, setDrafts] = useState<
    (ReplySuggestions & { conversationId: number }) | null
  >(null);
  const [isSuggesting, setIsSuggesting] = useState(false);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const statusMenuRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();
  const session = useMemo(() => readAuthSession(), []);
  const token = session?.access_token ?? "";
  /** The lead behind the open thread, shown in the same panel as the Lead Pool. */
  const [detailId, setDetailId] = useState<number | null>(null);
  const [isPropertiesOpen, setIsPropertiesOpen] = useState(false);
  /** Bumped whenever something in this tab might have added a history event —
      an action here, or the poll picking up one from elsewhere — so the
      history panel re-fetches without needing the lead to change. */
  const [historyTick, setHistoryTick] = useState(0);
  const viewer = session?.user ?? null;
  const isAgent = viewer?.role === "agent";
  const followUpScope = scope;

  /** A rejected token means the session is over; stop polling and sign out. */
  const handleApiError = useCallback(
    (error: unknown, fallback: string) => {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (error instanceof ApiRequestError && error.status === 401) {
        endSession();
        router.replace("/login?expired=1");
        return;
      }
      setErrorMessage(error instanceof Error ? error.message : fallback);
    },
    [router],
  );

  const active = followUps.find((item) => item.id === activeId) ?? null;
  // Suggestions belong to one thread's history; never show them on another.
  const ideas = drafts !== null && drafts.conversationId === activeId ? drafts : null;

  const refreshFollowUps = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      try {
        setFollowUps(
          await listFollowUps(
            token,
            followUpScope,
            undefined,
            signal,
            selectedCampaignId ? Number(selectedCampaignId) : undefined,
          ),
        );
        setErrorMessage("");
      } catch (error) {
        handleApiError(error, "Could not load your follow-ups.");
      } finally {
        setIsLoading(false);
      }
    },
    [token, followUpScope, selectedCampaignId, handleApiError],
  );

  const refreshMessages = useCallback(
    async (conversationId: number, signal?: AbortSignal) => {
      if (!token) return;
      try {
        setMessages(await listMessages(conversationId, token, signal));
      } catch (error) {
        handleApiError(error, "Could not load the conversation.");
      }
    },
    [token, handleApiError],
  );

  // Initial load.
  useEffect(() => {
    const controller = new AbortController();
    void refreshFollowUps(controller.signal);
    return () => controller.abort();
  }, [refreshFollowUps]);

  // Poll so a fresh owner reply, or Bobbie's answer, appears on its own.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshFollowUps();
      if (activeId !== null) void refreshMessages(activeId);
      // Bobbie can hand a thread back on her own (e.g. a meeting just booked),
      // so the history panel needs the same poll, not just the manual pills.
      setHistoryTick((tick) => tick + 1);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeId, refreshFollowUps, refreshMessages]);

  // Load a thread when the selection changes.
  useEffect(() => {
    if (activeId === null) {
      setMessages([]);
      return;
    }
    const controller = new AbortController();
    void refreshMessages(activeId, controller.signal);
    return () => controller.abort();
  }, [activeId, refreshMessages]);

  useEffect(() => {
    if (focusConversationId === null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setActiveId(focusConversationId);
    setScope("all");
  }, [focusConversationId]);

  // The campaign list is its own request: a sent campaign with no replies yet
  // still belongs in the box, and the replies obviously cannot supply it.
  // Drafts are left out — nothing has been sent, so they can never have a
  // reply, and offering one would only ever return an empty list.
  useEffect(() => {
    if (!token) return;
    const controller = new AbortController();
    listCampaigns(token, controller.signal)
      .then((rows) => setCampaigns(rows.filter((item) => item.status === "sent")))
      .catch(() => setCampaigns([]));
    return () => controller.abort();
  }, [token]);

  // Arriving from a campaign card shows every conversation from that campaign,
  // including owners who have not replied yet.
  useEffect(() => {
    if (campaignId === null) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedCampaignId(String(campaignId));
    setScope("all");
  }, [campaignId]);

  // Keep the newest message in view.
  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!isStatusMenuOpen) return;
    const close = (event: PointerEvent) => {
      if (!statusMenuRef.current?.contains(event.target as Node)) setIsStatusMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsStatusMenuOpen(false);
    };
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isStatusMenuOpen]);

  const needle = search.trim().toLowerCase();
  const selectedCampaign = campaigns.find((item) => String(item.id) === selectedCampaignId);
  const visible = followUps.filter((item) => {
    if (selectedCampaignId && item.campaign_id !== Number(selectedCampaignId)) return false;
    if (!needle) return true;
    return [item.name, item.contact, item.property_address]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  });
  const {
  markConversationAsRead,
} = useNotifications();

useEffect(() => {

  window.dispatchEvent(
    new CustomEvent(
      "listingiq-active-conversation",
      {
        detail: {
          conversationId:
            activeId,
        },
      },
    ),
  );

  if (
    "serviceWorker" in navigator
  ) {
    navigator.serviceWorker.ready
      .then((registration) => {
        registration.active?.postMessage({
          type:
            "ACTIVE_CONVERSATION",

          conversation_id:
            activeId,
        });
      })
      .catch((error) => {
        console.error(
          "[FollowUps] Failed to update active conversation in SW:",
          error,
        );
      });
  }


  if (activeId !== null) {
    void markConversationAsRead(
      activeId,
    );
  }


  return () => {

    window.dispatchEvent(
      new CustomEvent(
        "listingiq-active-conversation",
        {
          detail: {
            conversationId: null,
          },
        },
      ),
    );


    if (
      "serviceWorker" in navigator
    ) {
      void navigator.serviceWorker.ready
        .then((registration) => {
          registration.active?.postMessage({
            type:
              "ACTIVE_CONVERSATION",

            conversation_id:
              null,
          });
        });
    }
  };

}, [
  activeId,
  markConversationAsRead,
]);

  // The first message the assignee sent by hand is where Bobbie stepped aside.
  const takeoverId =
    messages.find((message) => message.event_type === "broker.message")?.id ?? null;

  /** Every action re-reads the row it changed, so the panel never drifts. */
  async function runAction(work: () => Promise<FollowUp>, success: string): Promise<void> {
    if (isWorking) return;
    setIsWorking(true);
    setErrorMessage("");
    try {
      const updated = await work();
      setFollowUps((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
      setNotice(success);
      setHistoryTick((tick) => tick + 1);
      if (activeId !== null) await refreshMessages(activeId);
    } catch (error) {
      handleApiError(error, "Could not update this follow-up.");
    } finally {
      setIsWorking(false);
    }
  }

  function changeStatus(leadStatus: LeadStatus): void {
    if (!active) return;
    void runAction(
      () => setFollowUpStatus(active.id, leadStatus, token),
      `Status set to ${LEAD_LABELS[leadStatus]}.`,
    );
  }

  /** Asks the server to draft next replies from this thread's history. */
  async function fetchIdeas(conversationId: number): Promise<void> {
    if (isSuggesting) return;
    setIsSuggesting(true);
    setErrorMessage("");
    try {
      const result = await suggestReplies(conversationId, token);
      setDrafts({ ...result, conversationId });
    } catch (error) {
      handleApiError(error, "Could not suggest a reply.");
    } finally {
      setIsSuggesting(false);
    }
  }

  /** Fills the composer with a draft. Nothing is sent until the broker submits. */
  function applyIdea(text: string): void {
    const box = composerRef.current;
    if (!box) return;
    box.value = text;
    box.focus();
    box.setSelectionRange(text.length, text.length);
  }

  /** Taking over pauses Bobbie; handing back is the only way she resumes. */
  function changeHandover(to: "bobbie" | "broker"): void {
    if (!active) return;
    const conversationId = active.id;
    void runAction(async () => {
      await setHandover(conversationId, to, token);
      const rows = await listFollowUps(token, scope);
      return rows.find((row) => row.id === conversationId) ?? active;
    }, to === "broker"
      ? "You have taken over. Write your reply below — Bobbie will not answer this thread."
      : "Bobbie is handling this conversation again.").then(() => {
        // Taking over is only useful if you can type, so land the cursor there
        // and offer drafts grounded in what has been said so far.
        if (to === "broker") {
          composerRef.current?.focus();
          void fetchIdeas(conversationId);
        } else {
          setDrafts(null);
        }
      });
  }

  /** Reply from this screen. The thread is already the assignee's by now. */
  async function handleSend(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSending || !active) return;

    const form = event.currentTarget;
    const text = String(new FormData(form).get("text") ?? "").trim();
    if (!text) return;

    setIsSending(true);
    setErrorMessage("");
    try {
      await sendMessage(active.id, text, token);
      form.reset();
      // The history just moved on, so the old drafts no longer answer it.
      setDrafts(null);
      await Promise.all([refreshMessages(active.id), refreshFollowUps()]);
    } catch (error) {
      handleApiError(error, "Could not send the message.");
    } finally {
      setIsSending(false);
    }
  }

  async function handleBook(payload: AppointmentPayload): Promise<void> {
    if (!active) return;
    const result = await bookAppointment(active.id, payload, token);
    setIsSchedulerOpen(false);
    setFollowUps((rows) =>
      rows.map((row) => (row.id === result.followup.id ? result.followup : row)),
    );
    setNotice(
      result.note ??
        (result.notified
          ? "The meeting is booked and the owner has the details."
          : "The meeting is booked."),
    );
    await refreshMessages(active.id);
  }

  return (
    <section className="followups-board">
      <header className="followups-header">
        <div>
          <span className="sms-eyebrow">CONVERSATIONS</span>
          <h1 className="view-title">Follow-ups</h1>
          <p>
            {viewer ? `${ROLE_LABELS[viewer.role] ?? "Workspace"} view — ${viewer.full_name}` : ""}
          </p>
        </div>
        <div className="dashboard-header-actions">
          <NotificationBell />
        </div>
      </header>

      <div className="followups-layout">
        <aside className="followups-list-panel">
          <div className="followups-scope" role="group" aria-label="Conversation scope">
            <button
              type="button"
              className={scope === "replied" ? "active" : undefined}
              aria-pressed={scope === "replied"}
              onClick={() => setScope("replied")}
            >
              Replied
            </button>
            <button
              type="button"
              className={scope === "all" ? "active" : undefined}
              aria-pressed={scope === "all"}
              onClick={() => setScope("all")}
            >
              All
            </button>
          </div>

          <label className="sms-search followups-search">
            <span className="sr-only">Search conversations</span>
            <input
              type="search"
              value={search}
              placeholder="Search name, number or address"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>

          <div className="followups-list-head">
            <div className="followups-campaign-filter">
              <span>Campaign</span>
              <div className="followups-campaign-select">
                <button
                  id="followup-campaign-filter"
                  type="button"
                  className="followups-campaign-box"
                  aria-haspopup="listbox"
                  aria-expanded={isCampaignMenuOpen}
                  onClick={() => setIsCampaignMenuOpen((current) => !current)}
                >
                  {selectedCampaign?.name ?? "All campaigns"}
                </button>
                {isCampaignMenuOpen ? (
                  <div className="followups-campaign-menu" role="listbox" aria-label="Campaign">
                    <button
                      type="button"
                      role="option"
                      aria-selected={selectedCampaignId === ""}
                      className={selectedCampaignId === "" ? "active" : undefined}
                      onClick={() => {
                        setSelectedCampaignId("");
                        setIsCampaignMenuOpen(false);
                      }}
                    >
                      <span>All campaigns</span>
                      {selectedCampaignId === "" ? <span aria-hidden="true">✓</span> : null}
                    </button>
                    {campaigns.map((item) => {
                      const selected = selectedCampaignId === String(item.id);
                      return (
                        <button
                          key={item.id}
                          type="button"
                          role="option"
                          aria-selected={selected}
                          className={selected ? "active" : undefined}
                          onClick={() => {
                            setSelectedCampaignId(String(item.id));
                            setIsCampaignMenuOpen(false);
                          }}
                        >
                          <span>{item.name}</span>
                          {selected ? <span aria-hidden="true">✓</span> : null}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            </div>
            <span className="sms-count" title={`${visible.length} conversations`}>
              {visible.length}
            </span>
          </div>

          <div className="followups-list" role="list">
            {isLoading ? <p className="sms-muted">Loading…</p> : null}
            {!isLoading && visible.length === 0 ? (
              <p className="sms-muted">
                {followUps.length === 0
                  ? followUpScope === "replied"
                    ? isAgent
                      ? "No owner replies are assigned to you yet."
                      : "No owner has replied yet. Switch to All to see every conversation."
                    : "No conversations yet. Start one to begin outreach."
                  : "No conversations match the current search and campaign filters."}
              </p>
            ) : null}

            {visible.map((item) => (
              <button
                key={item.id}
                type="button"
                role="listitem"
                className={`followup-card${item.id === activeId ? " active" : ""}`}
                onClick={() => setActiveId(item.id)}
              >
                <span className="followup-card-top">
                  <strong>{item.name || item.contact}</strong>
                  <small>{formatTime(item.last_reply_at ?? item.latest_message_at)}</small>
                </span>
                <span className="followup-card-meta">
                  {[item.property_address, item.area].filter(Boolean).join(" · ") || item.contact}
                </span>
                <span className={`followup-reason reason-${item.reason}`}>
                  {item.reply_count > 0 ? item.reason_label : "Waiting for first reply"}
                </span>
                {item.has_multiple_properties ? <span className="followup-multiple-signal">Multiple properties</span> : null}
                {item.followup_state === "pending" ? null : (
                  <span className={`followup-state-badge ${item.followup_state}`}>
                    {STATE_LABELS[item.followup_state]}
                  </span>
                )}
              </button>
            ))}
          </div>
        </aside>

        <div className={active?.reply_count === 0 ? "sms-thread-panel" : "followups-panel"}>
          {active ? (
            <>
              <header className={active.reply_count === 0 ? "sms-thread-header" : "followups-panel-header"}>
                <span className="sms-avatar large" aria-hidden="true">{initials(active)}</span>
                <div>
                  {active.reply_count === 0 ? (
                    <h3>{active.name || active.contact}</h3>
                  ) : (
                    <h2>{active.name || active.contact}</h2>
                  )}
                  <p>
                    {[active.property_address, active.area, active.contact]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                {active.has_multiple_properties ? (
                  <button
                    type="button"
                    className="followup-multiple-properties"
                    onClick={() => setIsPropertiesOpen(true)}
                    aria-label="View multiple properties for this owner"
                    data-tooltip="This person owns multiple properties that are expected to sell. Click to view all addresses."
                  >
                    <span aria-hidden="true">⌂</span>
                    <span className="sr-only">Multiple properties</span>
                  </button>
                ) : null}
                {active.handled_by === "bobbie" ? (
                  <button
                    type="button"
                    className="sms-pill on sms-pill-moving-border"
                    disabled={isWorking}
                    onClick={() => changeHandover("broker")}
                    title="Pause Bobbie and reply yourself"
                  >
                    <span aria-hidden="true">✦</span>
                    <span className="sms-pill-label">Bobbie AI is replying · Take over</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    className="sms-pill sms-pill-moving-border"
                    disabled={isWorking}
                    onClick={() => changeHandover("bobbie")}
                    title="Bobbie takes the conversation back"
                  >
                    <span aria-hidden="true">✎</span>
                    <span className="sms-pill-label">You are replying · Hand back to Bobbie</span>
                  </button>
                )}
                {/* Only an imported lead has a property record to open; a thread
                    started by hand carries nothing beyond what the header shows. */}
                {active.lead_id !== null ? (
                  <button
                    type="button"
                    className="sms-icon-button"
                    onClick={() => setDetailId(active.lead_id)}
                    aria-label={`View details for ${active.name || active.contact}`}
                    title="View property details"
                  >
                    ⓘ
                  </button>
                ) : null}
              </header>

              {active.reply_count > 0 ? (
                <p className="followups-summary">
                  <span className={`followup-reason reason-${active.reason}`}>
                    {active.reason_label}
                  </span>
                  <span>
                    {active.reply_count} {active.reply_count === 1 ? "reply" : "replies"}
                    {active.meeting_booked ? " · meeting booked" : ""}
                  </span>
                </p>
              ) : null}

              <div className="sms-thread followups-thread" ref={threadRef} aria-live="polite">
                {messages.length === 0 ? (
                  <p className="sms-muted">No messages on this thread.</p>
                ) : (
                  messages.map((message, index) => {
                    const day = dateLabel(message.created_at);
                    const showDay =
                      day !== "" && day !== dateLabel(messages[index - 1]?.created_at ?? null);
                    return (
                      <Fragment key={message.id}>
                        {showDay ? <div className="sms-day-divider">{day}</div> : null}
                        {message.id === takeoverId ? (
                          <div className="followups-event">
                            You took over · {formatClock(message.created_at)}
                          </div>
                        ) : null}
                        <article
                          className={`sms-row ${message.direction === "outbound" ? "outbound" : "inbound"}`}
                        >
                          {message.direction === "outbound" ? (
                            <span className="followups-channel">{channelLabel(message)}</span>
                          ) : null}
                          <div className="sms-bubble">{message.text}</div>
                          <div className="sms-meta">
                            <span className="sr-only">
                              {message.direction === "inbound" ? active.name || "Owner" : "Outbound"}
                            </span>
                            {message.direction === "outbound" && message.status ? (
                              <span>{deliveryLabel(message.status)} ·{" "}</span>
                            ) : null}
                            <time dateTime={message.created_at ?? undefined}>
                              {formatClock(message.created_at)}
                            </time>
                          </div>
                        </article>
                      </Fragment>
                    );
                  })
                )}
              </div>

              {/* Replied follow-ups require an explicit takeover. Before the
                  first reply this mirrors the SMS workspace: the composer is
                  available and sending makes the broker the thread's voice. */}
              {active.reply_count === 0 || active.handled_by === "broker" ? (
                <form className="sms-composer followups-composer" onSubmit={handleSend}>
                  <div className="followups-suggest-head">
                    <button
                      type="button"
                      className="sms-button-secondary followups-suggest"
                      onClick={() => void fetchIdeas(active.id)}
                      disabled={isSuggesting || isSending}
                    >
                      {isSuggesting
                        ? "Thinking…"
                        : ideas
                          ? "✦ Suggest again"
                          : "✦ Suggest from chat"}
                    </button>
                    {ideas ? (
                      <button
                        type="button"
                        className="followups-suggest-dismiss"
                        onClick={() => setDrafts(null)}
                        aria-label="Hide suggestions"
                        title="Hide suggestions"
                      >
                        ×
                      </button>
                    ) : null}
                  </div>
                  {ideas ? (
                    <div className="followups-ideas">
                      {ideas.note ? <p className="followups-ideas-note">{ideas.note}</p> : null}
                      <ul className="followups-ideas-list">
                        {ideas.suggestions.map((text) => (
                          <li key={text}>
                            <button type="button" onClick={() => applyIdea(text)}>
                              {text}
                            </button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                  <div className="followups-compose-field">
                    <textarea
                      ref={composerRef}
                      name="text"
                      rows={1}
                      maxLength={1600}
                      placeholder={`Write a message to ${active.name || active.contact}…`}
                      disabled={isSending}
                      required
                    />
                    <button
                      className="button followups-send-button"
                      type="submit"
                      disabled={isSending}
                      aria-label={isSending ? "Sending message" : "Send message"}
                      title={isSending ? "Sending…" : "Send message"}
                    >
                      {isSending ? (
                        <span className="followups-send-loading" aria-hidden="true">•••</span>
                      ) : (
                        <svg viewBox="0 0 20 20" aria-hidden="true">
                          <path d="M3 3.5 17 10 3 16.5l2-5.1L12 10l-7-1.4-2-5.1Z" />
                        </svg>
                      )}
                    </button>
                  </div>
                </form>
              ) : null}

              {active.reply_count > 0 ? (
                <footer className="followups-actions">
                <button
                  type="button"
                  className="sms-button-secondary"
                  disabled={isWorking || active.dnc_alert}
                  title={active.dnc_alert ? "This owner has opted out." : undefined}
                  onClick={() => setIsSchedulerOpen(true)}
                >
                  Schedule appointment
                </button>

                <div className="followups-status" ref={statusMenuRef}>
                  <span className="followups-status-label">Lead status</span>
                  <button
                    type="button"
                    className="followups-status-trigger"
                    disabled={isWorking}
                    aria-haspopup="listbox"
                    aria-expanded={isStatusMenuOpen}
                    onClick={() => setIsStatusMenuOpen((current) => !current)}
                  >
                    <span>{LEAD_LABELS[active.lead_status]}</span>
                    <span className="followups-status-chevron" aria-hidden="true" />
                  </button>
                  {isStatusMenuOpen ? (
                    <div className="followups-status-menu" role="listbox" aria-label="Lead status">
                      {LEAD_STATUS_ORDER.map((status) => {
                        const selected = active.lead_status === status;
                        return (
                          <button
                            key={status}
                            type="button"
                            role="option"
                            aria-selected={selected}
                            className={selected ? "active" : undefined}
                            onClick={() => {
                              setIsStatusMenuOpen(false);
                              if (!selected) changeStatus(status);
                            }}
                          >
                            <span>{LEAD_LABELS[status]}</span>
                            {selected ? <span aria-hidden="true">✓</span> : null}
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
                </footer>
              ) : null}
            </>
          ) : (
            <div className="sms-empty">
              <div aria-hidden="true">◎</div>
              <h3>Select a conversation</h3>
              <p>
                Pick a thread on the left to read the conversation and decide what happens next.
              </p>
            </div>
          )}
        </div>

        <aside className="followups-history-panel">
          <LeadTimeline
            leadId={active?.lead_id ?? null}
            accessToken={token}
            title={active ? "Lead history" : "History"}
            refreshToken={historyTick}
          />
        </aside>
      </div>

      {errorMessage ? <p className="sms-toast error" role="alert">{errorMessage}</p> : null}
      {notice ? (
        <p className="sms-toast" role="status" onAnimationEnd={() => setNotice("")}>
          {notice}
        </p>
      ) : null}


      <AppointmentDialog
        followUp={isSchedulerOpen ? active : null}
        accessToken={token}
        onClose={() => setIsSchedulerOpen(false)}
        onBook={handleBook}
      />

      {/* The thread is already open here, so the panel offers the property
          record only — no action that would lead back to this same screen. */}
      <LeadDetailDrawer
        leadId={detailId}
        accessToken={token}
        onClose={() => setDetailId(null)}
      />

      <div className={`leads-drawer-scrim${isPropertiesOpen ? " open" : ""}`} role="presentation" onClick={() => setIsPropertiesOpen(false)} />
      <aside className={`leads-drawer followup-properties-drawer${isPropertiesOpen ? " open" : ""}`} role="dialog" aria-modal="true" aria-label="Multiple property details" aria-hidden={!isPropertiesOpen}>
        <header className="leads-drawer-header">
          <div><span className="sms-eyebrow">MULTIPLE PROPERTIES</span><h3>{active?.name || active?.contact || "Owner"}</h3></div>
          <button type="button" className="sms-icon-button" onClick={() => setIsPropertiesOpen(false)} aria-label="Close property details">✕</button>
        </header>
        <div className="leads-drawer-body">
          <p className="followup-properties-intro">All property leads connected to {active?.contact} in this conversation.</p>
          {active?.properties.map((property, index) => (
            <section className="leads-drawer-section followup-property-card" key={property.lead_id}>
              <h4>Property {index + 1}</h4>
              <dl className="leads-facts">
                <div><dt>Address</dt><dd>{property.address || "Not on file"}</dd></div>
                <div><dt>Area</dt><dd>{property.area || "Not on file"}</dd></div>
                <div><dt>Campaign</dt><dd>{property.campaign_name || "Not in a campaign"}</dd></div>
              </dl>
              <div className="followup-property-signals">
                {property.signals.length ? property.signals.map((signal) => <span className="leads-signal" key={signal}>{signal}</span>) : <span className="leads-none">No signals</span>}
              </div>
            </section>
          ))}
        </div>
      </aside>
    </section>
  );
}
