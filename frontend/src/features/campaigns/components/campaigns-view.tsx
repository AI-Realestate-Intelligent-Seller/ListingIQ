"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { useRouter } from "next/navigation";

import { ConfirmDialog, type ConfirmRequest } from "@/components/dialog/confirm-dialog";
import { RetrievalProgress } from "@/components/loading/retrieval-progress";
import type { QueryProgress } from "@/lib/api/query-progress";
import { readAuthSession } from "@/features/auth/lib/auth-storage";
import { endSession } from "@/features/auth/lib/session-guard";
import { ApiRequestError } from "@/lib/api/http-client";

import { deleteCampaign, fetchCampaign, listCampaignsWithProgress } from "../api/campaigns-api";
import type { Campaign, CampaignDetail } from "../types/campaigns.types";
import { CampaignComposer } from "./campaign-composer";
import { NotificationBell } from "@/features/dashboard/components/notification-bell";

const CAMPAIGNS_PER_PAGE = 5;

type CampaignsViewProps = {
  /** Set when the Lead Pool has just drafted a campaign to compose. */
  draftId: number | null;
  /** Clears that draft once the broker leaves the composer. */
  onDraftHandled: () => void;
  /** Sends the broker to one campaign's replies in the Follow-ups tab. */
  onOpenFollowUps: (campaignId: number) => void;
  /** Opens one conversation in Follow-ups. */
  onOpenConversation: (conversationId: number) => void;
};

function formatDate(value: string | null): string {
  if (!value) return "—";
  const stamp = new Date(/[Z+]/.test(value) ? value : `${value}Z`);
  if (Number.isNaN(stamp.getTime())) return "—";
  return stamp.toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
}

/** Reply rate is the number the broker is actually judging a campaign on. */
function replyRate(campaign: Campaign): string {
  if (!campaign.delivered) return "—";
  return `${Math.round((campaign.replied / campaign.delivered) * 100)}%`;
}

/** Newest first, with the numeric id as a stable fallback for missing dates. */
function newestFirst(a: Campaign, b: Campaign): number {
  const aTime = new Date(a.sent_at ?? a.created_at ?? 0).getTime();
  const bTime = new Date(b.sent_at ?? b.created_at ?? 0).getTime();
  return bTime - aTime || b.id - a.id;
}

/**
 * The Campaigns tab. Two screens behind one route: the composer when a draft is
 * open, and otherwise the overview of how every campaign is performing.
 */
export function CampaignsView({
  draftId,
  onDraftHandled,
  onOpenFollowUps,
  onOpenConversation,
}: CampaignsViewProps) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [open, setOpen] = useState<CampaignDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadProgress, setLoadProgress] = useState<QueryProgress<Campaign[]> | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [confirmRequest, setConfirmRequest] = useState<ConfirmRequest | null>(null);
  const [sentPage, setSentPage] = useState(1);
  const [draftPage, setDraftPage] = useState(1);

  const router = useRouter();
  const session = useMemo(() => readAuthSession(), []);
  const token = session?.access_token ?? "";
  const isHob = session?.user.role === "hob";

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

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      if (!token) return;
      try {
        setCampaigns(await listCampaignsWithProgress(token, setLoadProgress, signal));
        setErrorMessage("");
      } catch (error) {
        handleApiError(error, "Could not load your campaigns.");
      } finally {
        if (!signal?.aborted) setIsLoading(false);
      }
    },
    [token, handleApiError],
  );

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  // The Lead Pool creates the draft and hands over its id; the composer opens
  // on the server's copy so the preview is never a guess about what was saved.
  useEffect(() => {
    if (draftId === null || !token) return;
    const controller = new AbortController();
    fetchCampaign(draftId, token, controller.signal)
      .then(setOpen)
      .catch((error: unknown) => handleApiError(error, "Could not open that campaign."));
    return () => controller.abort();
  }, [draftId, token, handleApiError]);

  function closeComposer(): void {
    setOpen(null);
    onDraftHandled();
    void refresh();
  }

  function openCampaign(campaignId: number): void {
    fetchCampaign(campaignId, token)
      .then(setOpen)
      .catch((error: unknown) => handleApiError(error, "Could not open that campaign."));
  }

  function confirmDiscard(campaign: CampaignDetail): void {
    setConfirmRequest({
      title: `Discard ${campaign.name}?`,
      body: "The draft is deleted and its leads go back to the pool. Nothing has been sent.",
      confirmLabel: "Discard draft",
      tone: "danger",
      onConfirm: async () => {
        await deleteCampaign(campaign.id, token);
        setNotice("Draft discarded. Its leads are back in the pool.");
        closeComposer();
      },
    });
  }

  if (open) {
    return (
      <>
        <CampaignComposer
          campaign={open}
          accessToken={token}
          onChange={setOpen}
          onSent={(result) => {
            const started = result.started.length;
            const skipped = result.skipped.length;
            setNotice(
              started > 0
                ? skipped > 0
                  ? "Campaign started, but some leads were skipped."
                  : "Campaign started successfully."
                : "No conversations were started.",
            );
            closeComposer();
          }}
          onDiscard={() => confirmDiscard(open)}
          onBack={closeComposer}
          onOpenConversation={onOpenConversation}
        />
        <ConfirmDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />
      </>
    );
  }

  const brokerFirst = (a: Campaign, b: Campaign): number =>
    (a.broker_name || "").localeCompare(b.broker_name || "") || newestFirst(a, b);
  const drafts = campaigns.filter((item) => item.status === "draft").sort(isHob ? brokerFirst : newestFirst);
  const sent = campaigns.filter((item) => item.status === "sent").sort(isHob ? brokerFirst : newestFirst);
  const sentPages = Math.max(1, Math.ceil(sent.length / CAMPAIGNS_PER_PAGE));
  const draftPages = Math.max(1, Math.ceil(drafts.length / CAMPAIGNS_PER_PAGE));
  const currentSentPage = Math.min(sentPage, sentPages);
  const currentDraftPage = Math.min(draftPage, draftPages);
  const visibleSent = sent.slice(
    (currentSentPage - 1) * CAMPAIGNS_PER_PAGE,
    currentSentPage * CAMPAIGNS_PER_PAGE,
  );
  const visibleDrafts = drafts.slice(
    (currentDraftPage - 1) * CAMPAIGNS_PER_PAGE,
    currentDraftPage * CAMPAIGNS_PER_PAGE,
  );

  return (
    <section className="campaigns-view">
      <header className="campaigns-header">
        <div>
          <span className="sms-eyebrow">OUTREACH</span>
          <h1 className="view-title">Campaigns</h1>
          <p>
            {campaigns.length === 0
              ? "Select leads in the Lead Pool and choose Create campaign to write your first message."
              : "Overview of Campaigns."}
          </p>
        </div>
        <NotificationBell />
      </header>

      {notice ? <p className="campaigns-notice" role="status">{notice}</p> : null}
      {errorMessage ? <p className="sms-error" role="alert">{errorMessage}</p> : null}
      {isLoading ? (
        <RetrievalProgress
          eyebrow="CAMPAIGNS"
          title="Retrieving campaigns"
          description="Preparing your campaign activity and results."
          status="Fetching campaigns for you"
          detail="Loading drafts, recipients, and performance totals"
          ariaLabel="Loading campaigns"
          progress={loadProgress}
          progressUnit="campaigns"
        />
      ) : null}

      <div className="campaigns-scroll-area">
      {sent.length > 0 ? (
        <div className="campaigns-card-grid">
          {visibleSent.map((campaign, index) => (
            <Fragment key={campaign.id}>
            {isHob && (index === 0 || visibleSent[index - 1]?.broker_id !== campaign.broker_id) ? (
              <div className="campaigns-broker-heading">
                <span>{campaign.broker_role === "hob" ? "HEAD OF BROKERAGE" : "AREA BROKER"}</span>
                <strong>{campaign.broker_name || campaign.broker_email || "Unknown broker"}</strong>
              </div>
            ) : null}
            <article className="campaigns-card" key={campaign.id}>
              <button
                type="button"
                className="campaigns-card-main"
                onClick={() => openCampaign(campaign.id)}
              >
                <strong className="campaigns-card-name">{campaign.name}</strong>
                <span className="campaigns-card-meta">
                  Created {formatDate(campaign.created_at)} · {campaign.recipients}{" "}
                  {campaign.recipients === 1 ? "lead" : "leads"} ·{" "}
                  <span className="campaigns-card-status">Active</span>
                </span>
                {campaign.not_sent > 0 ? (
                  <span className="campaigns-card-warning">
                    {campaign.not_sent} could not be texted
                  </span>
                ) : null}
              </button>

              <div className="campaigns-card-stats" aria-label={`${campaign.name} performance`}>
                <span className="campaigns-card-stat">
                  <b>{campaign.delivered}</b>
                  <span>Delivered</span>
                </span>
                <span className="campaigns-card-stat">
                  <b>{campaign.replied}</b>
                  <span>Replied</span>
                </span>
                <span className="campaigns-card-stat">
                  <b>{campaign.no_reply}</b>
                  <span>No reply</span>
                </span>
                <span className="campaigns-card-stat">
                  <b>{replyRate(campaign)}</b>
                  <span>Reply rate</span>
                </span>
              </div>

              <button
                type="button"
                className="sms-button-secondary campaigns-card-action"
                onClick={() => onOpenFollowUps(campaign.id)}
              >
                View replies
              </button>
            </article>
            </Fragment>
          ))}
        </div>
      ) : null}

      {sentPages > 1 ? (
        <nav className="campaigns-pagination" aria-label="Campaign pages">
          <button
            type="button"
            onClick={() => setSentPage(currentSentPage - 1)}
            disabled={currentSentPage === 1}
          >
            Previous
          </button>
          <span>Page {currentSentPage} of {sentPages}</span>
          <button
            type="button"
            onClick={() => setSentPage(currentSentPage + 1)}
            disabled={currentSentPage === sentPages}
          >
            Next
          </button>
        </nav>
      ) : null}

      {drafts.length > 0 ? (
        <div className="campaigns-drafts">
          <h3>Drafts</h3>
          <div className="campaigns-card-grid">
            {visibleDrafts.map((campaign, index) => (
              <Fragment key={campaign.id}>
              {isHob && (index === 0 || visibleDrafts[index - 1]?.broker_id !== campaign.broker_id) ? (
                <div className="campaigns-broker-heading">
                  <span>{campaign.broker_role === "hob" ? "HEAD OF BROKERAGE" : "AREA BROKER"}</span>
                  <strong>{campaign.broker_name || campaign.broker_email || "Unknown broker"}</strong>
                </div>
              ) : null}
              <article className="campaigns-card campaigns-draft-card" key={campaign.id}>
                <button
                  type="button"
                  className="campaigns-card-main"
                  onClick={() => openCampaign(campaign.id)}
                >
                  <strong className="campaigns-card-name">{campaign.name}</strong>
                  <span className="campaigns-card-meta">
                    Created {formatDate(campaign.created_at)} · {campaign.recipients}{" "}
                    {campaign.recipients === 1 ? "lead" : "leads"} ·{" "}
                    <span className="campaigns-card-status campaigns-card-status-draft">Draft</span>
                  </span>
                </button>

                <div className="campaigns-card-stats" aria-label={`${campaign.name} draft details`}>
                  <span className="campaigns-card-stat">
                    <b>{campaign.recipients}</b>
                    <span>Waiting</span>
                  </span>
                </div>

                <button
                  type="button"
                  className="sms-button-secondary campaigns-card-action"
                  onClick={() => openCampaign(campaign.id)}
                >
                  Finish writing →
                </button>
              </article>
              </Fragment>
            ))}
          </div>
        </div>
      ) : null}

      {draftPages > 1 ? (
        <nav className="campaigns-pagination" aria-label="Draft campaign pages">
          <button
            type="button"
            onClick={() => setDraftPage(currentDraftPage - 1)}
            disabled={currentDraftPage === 1}
          >
            Previous
          </button>
          <span>Page {currentDraftPage} of {draftPages}</span>
          <button
            type="button"
            onClick={() => setDraftPage(currentDraftPage + 1)}
            disabled={currentDraftPage === draftPages}
          >
            Next
          </button>
        </nav>
      ) : null}

      {!isLoading && campaigns.length === 0 ? (
        <div className="dashboard-empty">
          <div>✦</div>
          <h3>No campaigns yet</h3>
          <p>
            Open the Lead Pool, select the owners you want to reach, and choose Create campaign.
          </p>
        </div>
      ) : null}
      </div>

      <ConfirmDialog request={confirmRequest} onClose={() => setConfirmRequest(null)} />
    </section>
  );
}
