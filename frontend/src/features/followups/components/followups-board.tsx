"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { ConfirmDialog, type ConfirmRequest } from "@/components/dialog/confirm-dialog";
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

import { bookAppointment, listFollowUps, setFollowUpState, setFollowUpStatus } from "../api/followups-api";
import type { AppointmentPayload, FollowUp, FollowUpState } from "../types/followups.types";
import { AppointmentDialog } from "./appointment-dialog";

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
  delivered: "Delivered",
  delivery_failed: "Not delivered",
  sending_failed: "Not sent",
  received: "Received",
};

function deliveryLabel(status: string): string {
  return DELIVERY_LABELS[status] ?? status.replaceAll("_", " ");
}

function isDeliveryFailure(status: string | null): boolean {
  return /fail|reject|undeliver/i.test(status ?? "");
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
};

export function FollowUpsBoard({ focusConversationId = null }: FollowUpsBoardProps) {
  const [followUps, setFollowUps] = useState<FollowUp[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  const [stateFilter, setStateFilter] = useState<FollowUpState | "all">("all");
  const [isLoading, setIsLoading] = useState(true);
  const [isWorking, setIsWorking] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isSchedulerOpen, setIsSchedulerOpen] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const router = useRouter();
  const session = useMemo(() => readAuthSession(), []);
  const token = session?.access_token ?? "";
  const viewer = session?.user ?? null;

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

  const refreshFollowUps = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      try {
        setFollowUps(await listFollowUps(token, undefined, signal));
        setErrorMessage("");
      } catch (error) {
        handleApiError(error, "Could not load your follow-ups.");
      } finally {
        setIsLoading(false);
      }
    },
    [token, handleApiError],
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
  }, [focusConversationId]);

  // Keep the newest message in view.
  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [messages]);

  const visible = followUps.filter(
    (item) => stateFilter === "all" || item.followup_state === stateFilter,
  );

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
      if (activeId !== null) await refreshMessages(activeId);
    } catch (error) {
      handleApiError(error, "Could not update this follow-up.");
    } finally {
      setIsWorking(false);
    }
  }

  function decide(state: FollowUpState): void {
    if (!active) return;
    const followUp = active;
    if (state === "declined") {
      setConfirmRequest({
        title: `Decline ${followUp.name || followUp.contact}?`,
        body: "The conversation is kept, but this lead is recorded as one you are not working. You can accept it later.",
        confirmLabel: "Decline lead",
        tone: "danger",
        onConfirm: () =>
          runAction(
            () => setFollowUpState(followUp.id, "declined", token),
            `${followUp.name || followUp.contact} was declined.`,
          ),
      });
      return;
    }
    void runAction(
      () => setFollowUpState(followUp.id, state, token),
      state === "accepted"
        ? `You accepted ${followUp.name || followUp.contact}.`
        : "The decision was cleared.",
    );
  }

  function changeStatus(leadStatus: LeadStatus): void {
    if (!active) return;
    void runAction(
      () => setFollowUpStatus(active.id, leadStatus, token),
      `Status set to ${LEAD_LABELS[leadStatus]}.`,
    );
  }

  /** Taking over pauses Bobbie; handing back is the only way she resumes. */
  function changeHandover(to: "bobbie" | "broker"): void {
    if (!active) return;
    const conversationId = active.id;
    void runAction(async () => {
      await setHandover(conversationId, to, token);
      const rows = await listFollowUps(token);
      return rows.find((row) => row.id === conversationId) ?? active;
    }, to === "broker"
      ? "You have taken over. Write your reply below — Bobbie will not answer this thread."
      : "Bobbie is handling this conversation again.").then(() => {
        // Taking over is only useful if you can type, so land the cursor there.
        if (to === "broker") composerRef.current?.focus();
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
        <span className="sms-eyebrow">CONVERSATIONS</span>
        <h1>Follow-ups</h1>
        <p>
          {viewer ? `${ROLE_LABELS[viewer.role] ?? "Workspace"} view — ${viewer.full_name}, ` : ""}
          showing every conversation where the owner has replied
        </p>
      </header>

      <div className="followups-layout">
        <aside className="followups-list-panel">
          <div className="followups-list-head">
            <label className="sr-only" htmlFor="followup-state-filter">
              Filter follow-ups
            </label>
            <select
              id="followup-state-filter"
              value={stateFilter}
              onChange={(event) => setStateFilter(event.target.value as FollowUpState | "all")}
            >
              <option value="all">All replies</option>
              <option value="pending">Awaiting a decision</option>
              <option value="accepted">Accepted</option>
              <option value="declined">Declined</option>
            </select>
            <span className="sms-count">{visible.length}</span>
          </div>

          <div className="followups-list" role="list">
            {isLoading ? <p className="sms-muted">Loading…</p> : null}
            {!isLoading && visible.length === 0 ? (
              <p className="sms-muted">
                {followUps.length === 0
                  ? "No owner has replied yet. Replies land here as soon as they arrive."
                  : "No follow-ups match that filter."}
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
                  <small>{formatTime(item.last_reply_at)}</small>
                </span>
                <span className="followup-card-meta">
                  {[item.property_address, item.area].filter(Boolean).join(" · ") || item.contact}
                </span>
                <span className={`followup-reason reason-${item.reason}`}>{item.reason_label}</span>
                {item.followup_state === "pending" ? null : (
                  <span className={`followup-state-badge ${item.followup_state}`}>
                    {STATE_LABELS[item.followup_state]}
                  </span>
                )}
              </button>
            ))}
          </div>
        </aside>

        <div className="followups-panel">
          {active ? (
            <>
              <header className="followups-panel-header">
                <span className="sms-avatar large" aria-hidden="true">{initials(active)}</span>
                <div>
                  <h2>{active.name || active.contact}</h2>
                  <p>
                    {[active.property_address, active.area, active.contact]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
                <label className="followups-status">
                  <span className="sr-only">Lead status</span>
                  <select
                    value={active.lead_status}
                    disabled={isWorking}
                    onChange={(event) => changeStatus(event.target.value as LeadStatus)}
                  >
                    {LEAD_STATUS_ORDER.map((status) => (
                      <option key={status} value={status}>
                        {LEAD_LABELS[status]}
                      </option>
                    ))}
                  </select>
                </label>
              </header>

              <p className="followups-summary">
                <span className={`followup-reason reason-${active.reason}`}>{active.reason_label}</span>
                <span>
                  {active.reply_count} {active.reply_count === 1 ? "reply" : "replies"} ·{" "}
                  {active.handled_by === "bobbie" ? "Bobbie is replying" : "You are replying"}
                  {active.meeting_booked ? " · meeting booked" : ""}
                </span>
              </p>

              <div className="sms-thread followups-thread" ref={threadRef} aria-live="polite">
                {messages.length === 0 ? (
                  <p className="sms-muted">No messages on this thread.</p>
                ) : (
                  messages.map((message, index) => {
                    const day = dateLabel(message.created_at);
                    const showDay =
                      day !== "" && day !== dateLabel(messages[index - 1]?.created_at ?? null);
                    const failed = isDeliveryFailure(message.status);
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
                          <div className={`sms-meta${failed ? " failed" : ""}`}>
                            <span className="sr-only">
                              {message.direction === "inbound" ? active.name || "Owner" : "Outbound"}
                            </span>
                            {message.direction === "outbound" && message.status ? (
                              <span>{deliveryLabel(message.status)} · </span>
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

              {/* The composer exists only once the thread is yours: two voices
                  on one thread is worse than an extra click, so replying is
                  gated on the same handover the SMS workspace uses. */}
              {active.handled_by === "broker" ? (
                <form className="sms-composer followups-composer" onSubmit={handleSend}>
                  <textarea
                    ref={composerRef}
                    name="text"
                    rows={2}
                    maxLength={1600}
                    placeholder={`Write a message to ${active.name || active.contact}…`}
                    disabled={isSending}
                    required
                  />
                  <div className="sms-composer-actions">
                    <span className="sms-composer-hint">
                      You are handling this conversation. Bobbie will not reply here.
                    </span>
                    <button className="button" type="submit" disabled={isSending}>
                      {isSending ? "Sending…" : "Send"}
                    </button>
                  </div>
                </form>
              ) : null}

              <footer className="followups-actions">
                {active.followup_state === "accepted" ? (
                  <button
                    type="button"
                    className="sms-button-secondary"
                    disabled={isWorking}
                    onClick={() => decide("pending")}
                  >
                    ✓ Accepted · undo
                  </button>
                ) : (
                  <button
                    type="button"
                    className="button"
                    style={{ backgroundColor: "#176b5b" }}
                    disabled={isWorking}
                    onClick={() => decide("accepted")}
                  >
                    Accept lead
                  </button>
                )}

                {active.handled_by === "bobbie" ? (
                  <button
                    type="button"
                    className="sms-button-secondary"
                    disabled={isWorking}
                    onClick={() => changeHandover("broker")}
                  >
                    Take over conversation
                  </button>
                ) : (
                  <button
                    type="button"
                    className="sms-button-secondary"
                    disabled={isWorking}
                    onClick={() => changeHandover("bobbie")}
                  >
                    Hand back to Bobbie
                  </button>
                )}

                <button
                  type="button"
                  className="sms-button-secondary"
                  disabled={isWorking || active.dnc_alert}
                  title={active.dnc_alert ? "This owner has opted out." : undefined}
                  onClick={() => setIsSchedulerOpen(true)}
                >
                  Schedule appointment
                </button>

                {active.followup_state === "declined" ? (
                  <button
                    type="button"
                    className="followups-decline"
                    disabled={isWorking}
                    onClick={() => decide("pending")}
                  >
                    Declined · undo
                  </button>
                ) : (
                  <button
                    type="button"
                    className="followups-decline"
                    disabled={isWorking}
                    onClick={() => decide("declined")}
                  >
                    Decline
                  </button>
                )}
              </footer>
            </>
          ) : (
            <div className="sms-empty">
              <div aria-hidden="true">◎</div>
              <h3>Select a follow-up</h3>
              <p>
                Every owner who has answered appears on the left. Pick one to read the thread and
                decide what happens next.
              </p>
            </div>
          )}
        </div>
      </div>

      {errorMessage ? <p className="sms-toast error" role="alert">{errorMessage}</p> : null}
      {notice ? (
        <p className="sms-toast" role="status" onAnimationEnd={() => setNotice("")}>
          {notice}
        </p>
      ) : null}

      <ConfirmDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />

      <AppointmentDialog
        followUp={isSchedulerOpen ? active : null}
        accessToken={token}
        onClose={() => setIsSchedulerOpen(false)}
        onBook={handleBook}
      />
    </section>
  );
}
