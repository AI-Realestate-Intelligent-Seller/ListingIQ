"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useRouter } from "next/navigation";

import { readAuthSession } from "@/features/auth/lib/auth-storage";

import { endSession } from "@/features/auth/lib/session-guard";
import { ConfirmDialog, type ConfirmRequest } from "@/components/dialog/confirm-dialog";
import { ApiRequestError } from "@/lib/api/http-client";
import { LeadDetailDrawer } from "@/features/leads/components/lead-detail-drawer";

import {
  deleteConversation,
  listConversations,
  listMessages,
  sendMessage,
  setHandover,
} from "../api/sms-api";
import { LEAD_LABELS, dateLabel, formatTime, initials } from "../lib/sms-format";
import type {
  SmsConversation,
  SmsMessage,
} from "../types/sms.types";

/** How often the open thread and the conversation list refresh. */
const POLL_INTERVAL_MS = 5000;

/** Who a message came from, derived from direction and the event that produced it. */
function senderLabel(message: SmsMessage, conversation: SmsConversation): string {
  if (message.direction === "inbound") return conversation.name || "Owner";
  if (message.event_type === "broker.message") return "You";
  if (message.event_type === "broker.booking") return "You · booking";
  if (message.event_type === "calendar.confirmation") return "Bobbie · booking";
  return "Bobbie";
}

function deliveryLabel(status: string): string {
  if (/^(queued|sending)$/i.test(status)) return status.toLowerCase() === "queued" ? "Queued" : "Sending";
  return "Sent";
}

type SmsWorkspaceProps = {
  /** A thread to open on arrival, e.g. from the Lead Pool details panel. */
  focusConversationId?: number | null;
};

export function SmsWorkspace({ focusConversationId = null }: SmsWorkspaceProps) {
  const [conversations, setConversations] = useState<SmsConversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [messages, setMessages] = useState<SmsMessage[]>([]);
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSending, setIsSending] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  /** Mobile only: the conversation list is an off-canvas drawer over the thread. */
  const [isListOpen, setIsListOpen] = useState(false);
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  /** The lead behind the open thread, shown in the same panel as the Lead Pool. */
  const [detailId, setDetailId] = useState<number | null>(null);

  const threadRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();
  const token = useMemo(() => readAuthSession()?.access_token ?? "", []);

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

  const active = conversations.find((item) => item.id === activeId) ?? null;

  const refreshConversations = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      try {
        const rows = await listConversations(token, signal);
        setConversations(rows);
        setErrorMessage("");
      } catch (error) {
        handleApiError(error, "Could not load conversations.");
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
        handleApiError(error, "Could not load messages.");
      }
    },
    [token, handleApiError],
  );

  // Initial load.
  useEffect(() => {
    const controller = new AbortController();
    void refreshConversations(controller.signal);
    return () => controller.abort();
  }, [refreshConversations]);

  // Poll the list and the open thread so Bobbie's replies appear on their own.
  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshConversations();
      if (activeId !== null) void refreshMessages(activeId);
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeId, refreshConversations, refreshMessages]);

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

  // Arriving from the Lead Pool with a thread in mind.
  useEffect(() => {
    if (focusConversationId === null) return;
    setActiveId(focusConversationId);
    setIsListOpen(false);
  }, [focusConversationId]);

  // Escape closes the mobile drawer, as with any overlay.
  useEffect(() => {
    if (!isListOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsListOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isListOpen]);

  /** Picking a thread returns the phone to the chat it just opened. */
  function openConversation(conversationId: number): void {
    setActiveId(conversationId);
    setIsListOpen(false);
  }

  // Keep the newest message in view.
  useEffect(() => {
    const thread = threadRef.current;
    if (thread) thread.scrollTop = thread.scrollHeight;
  }, [messages]);

  async function handleSend(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isSending || activeId === null) return;

    const form = event.currentTarget;
    const field = new FormData(form);
    const text = String(field.get("text") ?? "").trim();
    if (!text) return;

    setIsSending(true);
    setErrorMessage("");
    try {
      await sendMessage(activeId, text, token);
      form.reset();
      await Promise.all([refreshMessages(activeId), refreshConversations()]);
    } catch (error) {
      handleApiError(error, "Could not send the message.");
    } finally {
      setIsSending(false);
    }
  }

  async function changeHandover(to: "bobbie" | "broker"): Promise<void> {
    if (!active) return;
    try {
      await setHandover(active.id, to, token);
      setNotice(
        to === "bobbie"
          ? "Bobbie is handling this conversation again."
          : "You have taken over. Bobbie will not reply on this thread.",
      );
      await refreshConversations();
    } catch (error) {
      handleApiError(error, "Could not update the conversation.");
    }
  }

  function confirmRemoveConversation(): void {
    if (!active) return;
    const conversationId = active.id;
    setConfirmRequest({
      title: `Delete the conversation with ${active.name || active.contact}?`,
      body: "Every message in this thread is deleted. If the lead is still in your pool it returns to Ready for Outreach.",
      confirmLabel: "Delete conversation",
      tone: "danger",
      onConfirm: async () => {
        await deleteConversation(conversationId, token);
        setActiveId(null);
        await refreshConversations();
      },
    });
  }

  const visible = conversations.filter((item) => {
    const needle = search.trim().toLowerCase();
    if (!needle) return true;
    return [item.name, item.contact, item.property_address]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  });

  return (
    <section className={`sms-workspace${isListOpen ? " list-open" : ""}`}>
      {/* Only ever visible on phones, where the list is an overlay. */}
      <div
        className="sms-drawer-scrim"
        role="presentation"
        onClick={() => setIsListOpen(false)}
      />

      <aside className="sms-list-panel" id="sms-conversation-list">
        <header>
          <div>
            <span className="sms-eyebrow">SMS</span>
            <h2>Conversations</h2>
          </div>
          <span className="sms-count">{conversations.length}</span>
          <button
            type="button"
            className="sms-icon-button sms-drawer-close"
            onClick={() => setIsListOpen(false)}
            aria-label="Close conversation list"
          >
            ✕
          </button>
        </header>

        <label className="sms-search">
          <span className="sr-only">Search conversations</span>
          <input
            type="search"
            value={search}
            placeholder="Search name, number or address"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>

        <div className="sms-list" role="list">
          {isLoading ? <p className="sms-muted">Loading…</p> : null}
          {!isLoading && visible.length === 0 ? (
            <p className="sms-muted">
              {conversations.length === 0
                ? "No conversations yet. Start one to let Bobbie reach out."
                : "No conversations match that search."}
            </p>
          ) : null}

          {visible.map((item) => (
            <button
              key={item.id}
              type="button"
              role="listitem"
              className={`sms-list-item${item.id === activeId ? " active" : ""}`}
              onClick={() => openConversation(item.id)}
            >
              <span className="sms-avatar" aria-hidden="true">{initials(item)}</span>
              <span className="sms-list-body">
                <span className="sms-list-top">
                  <strong>{item.name || item.contact}</strong>
                  <small>{formatTime(item.latest_message_at)}</small>
                </span>
                <span className="sms-list-preview">{item.latest_message || "No messages yet"}</span>
                <span className="sms-badges">
                  {item.awaiting_broker_reply ? (
                    <span className="sms-badge awaiting">● Needs your reply</span>
                  ) : null}
                  <span className={`sms-badge status-${item.lead_status}`}>
                    {LEAD_LABELS[item.lead_status] ?? item.lead_status}
                  </span>
                  <span className={`sms-badge ${item.handled_by === "bobbie" ? "bobbie" : "broker"}`}>
                    {item.handled_by === "bobbie" ? "✦ Bobbie" : "You"}
                  </span>
                  {item.meeting_booked ? <span className="sms-badge booked">Meeting booked</span> : null}
                  {item.dnc_alert ? <span className="sms-badge dnc">DNC</span> : null}
                </span>
              </span>
            </button>
          ))}
        </div>
      </aside>

      <div className="sms-thread-panel">
        {active ? (
          <>
            <header className="sms-thread-header">
              <button
                type="button"
                className="sms-icon-button sms-drawer-button"
                onClick={() => setIsListOpen(true)}
                aria-label="Show conversations"
                aria-expanded={isListOpen}
                aria-controls="sms-conversation-list"
              >
                ☰
              </button>
              <span className="sms-avatar large" aria-hidden="true">{initials(active)}</span>
              <div>
                <h3>{active.name || active.contact}</h3>
                <p>
                  {active.contact}
                  {active.property_address ? ` · ${active.property_address}` : ""}
                </p>
              </div>
              {/* On narrow screens the label collapses and the glyph alone remains,
                  so the aria-label carries the meaning either way. */}
              {active.handled_by === "bobbie" ? (
                <button
                  type="button"
                  className="sms-pill on sms-pill-moving-border"
                  onClick={() => changeHandover("broker")}
                  title="Pause Bobbie and reply yourself"
                  aria-label="Bobbie AI is replying. Take over."
                >
                  <span aria-hidden="true">✦</span>
                  <span className="sms-pill-label">Bobbie AI is replying · Take over</span>
                </button>
              ) : (
                <button
                  type="button"
                  className="sms-pill sms-pill-moving-border"
                  onClick={() => changeHandover("bobbie")}
                  title="Bobbie takes the conversation back"
                  aria-label="You are replying. Hand back to Bobbie."
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
              <button
                type="button"
                className="sms-icon-button"
                onClick={confirmRemoveConversation}
                aria-label="Delete conversation"
                title="Delete conversation"
              >
                🗑
              </button>
            </header>

            {active.awaiting_broker_reply ? (
              <p className="sms-attention" role="status">
                {active.lead_status === "location_discussion" ? (
                  <><strong>Location to confirm.</strong> Agree the location before sending appointment details.</>
                ) : (
                  <><strong>Waiting for you.</strong> Bobbie has stepped back on this
                    thread, so this reply will not be answered automatically.</>
                )}
              </p>
            ) : null}

            <div className="sms-thread" ref={threadRef} aria-live="polite">
              {messages.length === 0 ? (
                <p className="sms-muted">No messages yet.</p>
              ) : (
                messages.map((message, index) => {
                  const day = dateLabel(message.created_at);
                  const showDay = day !== "" && day !== dateLabel(messages[index - 1]?.created_at ?? null);
                  return (
                    <Fragment key={message.id}>
                      {showDay ? <div className="sms-day-divider">{day}</div> : null}
                      <article
                        className={`sms-row ${message.direction === "outbound" ? "outbound" : "inbound"}`}
                      >
                        <div className="sms-bubble">{message.text}</div>
                        {/* A plain div, not <footer>: the global marketing stylesheet paints
                            every bare <footer> with a dark background. */}
                        <div className="sms-meta">
                          {/* Sight users read the sender from the bubble's side and colour. */}
                          <span className="sr-only">{senderLabel(message, active)}</span>
                          <time dateTime={message.created_at ?? undefined}>
                            {formatTime(message.created_at)}
                          </time>
                          {message.direction === "outbound" && message.status ? (
                            <span>· {deliveryLabel(message.status)}</span>
                          ) : null}
                        </div>
                      </article>
                    </Fragment>
                  );
                })
              )}
            </div>

            <form className="sms-composer" onSubmit={handleSend}>
              <textarea
                name="text"
                rows={2}
                maxLength={1600}
                placeholder="Write a message to the owner…"
                disabled={isSending}
                required
              />
              <div className="sms-composer-actions">
                <span className="sms-composer-hint">
                  {active.handled_by === "bobbie"
                    ? "Sending takes the thread off Bobbie."
                    : "You are handling this conversation."}
                </span>
                <button className="button" type="submit" style={{ backgroundColor: " #176b5b" }} disabled={isSending}>
                      {isSending ? "Sending…" : "Send"}
                    </button>
              </div>
            </form>
          </>
        ) : (
          <div className="sms-empty">
            <div aria-hidden="true">◎</div>
            <h3>Select a conversation</h3>
            <p>Pick a thread on the left to read the conversation.</p>
            <button
              type="button"
              className="sms-button-secondary sms-drawer-button"
              onClick={() => setIsListOpen(true)}
            >
              ☰ Browse conversations
            </button>
          </div>
        )}
      </div>

      {errorMessage ? <p className="sms-toast error" role="alert">{errorMessage}</p> : null}
      {notice ? (
        <p className="sms-toast" role="status" onAnimationEnd={() => setNotice("")}>
          {notice}
        </p>
      ) : null}

      <ConfirmDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />

      {/* The thread is already open here, so the panel offers the property
          record only — no action that would lead back to this same screen. */}
      <LeadDetailDrawer
        leadId={detailId}
        accessToken={token}
        onClose={() => setDetailId(null)}
      />
    </section>
  );
}
