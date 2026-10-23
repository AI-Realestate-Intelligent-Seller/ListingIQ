import { ApiRequestError, deleteJson, getJson, patchJson, postJson } from "@/lib/api/http-client";
import { loadQueryWithProgress, type QueryProgress } from "@/lib/api/query-progress";

import type {
  Campaign,
  CampaignDetail,
  CampaignDraftResult,
  CampaignFilters,
  CampaignSelection,
  CampaignReasonSuggestions,
  CampaignSendResult,
} from "../types/campaigns.types";

const CAMPAIGNS = "/campaigns";

function campaignPreviewParams(previewPage: number, filters: CampaignFilters): URLSearchParams {
  const params = new URLSearchParams({ preview_page: String(previewPage) });
  for (const value of filters.signals ?? []) params.append("signal", value);
  if (filters.no_signal) params.set("no_signal", "true");
  if (filters.main_signal) params.set("main_signal", filters.main_signal);
  if (filters.area) params.set("area", filters.area);
  if (filters.search) params.set("search", filters.search);
  return params;
}

/** Every campaign with its delivered / replied / no-reply counts. */
export function listCampaigns(accessToken: string, signal?: AbortSignal): Promise<Campaign[]> {
  return getJson<Campaign[]>(CAMPAIGNS, accessToken, signal);
}

export function listCampaignsWithProgress(
  accessToken: string,
  onProgress: (progress: QueryProgress<Campaign[]>) => void,
  signal?: AbortSignal,
): Promise<Campaign[]> {
  return loadQueryWithProgress({
    startPath: `${CAMPAIGNS}/load`,
    fallback: () => listCampaigns(accessToken, signal),
    accessToken,
    signal,
    onProgress,
  });
}

export function fetchCampaign(
  campaignId: number,
  accessToken: string,
  signal?: AbortSignal,
  previewPage = 1,
  filters: CampaignFilters = {},
): Promise<CampaignDetail> {
  const params = campaignPreviewParams(previewPage, filters);
  return getJson<CampaignDetail>(
    `${CAMPAIGNS}/${campaignId}?${params.toString()}`,
    accessToken,
    signal,
  );
}

/**
 * Turns a Lead Pool selection into a draft. Nothing is sent — the broker names
 * the campaign and writes the message in the composer first.
 */
export function createCampaignDraft(
  leadIds: number[],
  accessToken: string,
): Promise<CampaignDraftResult> {
  return createCampaignDraftInBackground(leadIds, accessToken);
}

async function createCampaignDraftInBackground(
  leadIds: number[],
  accessToken: string,
): Promise<CampaignDraftResult> {
  const payload = { lead_ids: leadIds };
  let jobId: string;
  try {
    const started = await postJson<{ job_id: string }, typeof payload>(
      `${CAMPAIGNS}/draft/load`,
      payload,
      accessToken,
    );
    jobId = started.job_id;
  } catch (error) {
    // Older deployments and a known Redis outage have definitely not queued a
    // job, so the established synchronous endpoint remains a safe fallback.
    if (error instanceof ApiRequestError && (error.status === 404 || error.status === 503)) {
      return postJson<CampaignDraftResult, typeof payload>(
        `${CAMPAIGNS}/draft`, payload, accessToken,
      );
    }
    throw error;
  }

  for (;;) {
    const progress = await getJson<QueryProgress<CampaignDraftResult>>(
      `/query-progress/${jobId}`,
      accessToken,
    );
    if (progress.status === "completed") {
      if (progress.result !== null) return progress.result;
      throw new ApiRequestError("Campaign validation completed without a result.");
    }
    if (progress.status === "failed") {
      throw new ApiRequestError(progress.error || "Campaign validation failed.");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 150));
  }
}

/** Rename the campaign, rewrite its message or reason. Returns the fresh preview. */
export function updateCampaign(
  campaignId: number,
  changes: {
    name?: string;
    message_template?: string;
    outreach_reason?: string;
    reason_group?: { signal: string; reason: string; confirmed: boolean };
  },
  accessToken: string,
  previewPage = 1,
): Promise<CampaignDetail> {
  return patchJson<CampaignDetail, typeof changes>(
    `${CAMPAIGNS}/${campaignId}?preview_page=${previewPage}`,
    changes,
    accessToken,
  );
}

/**
 * Asks DeepSeek to phrase the reason this set of leads has in common. POST
 * because each call spends a request; the server falls back to its built-in
 * wordings when DeepSeek is unconfigured or unreachable, so this never fails
 * just because the network did.
 */
export function suggestReasons(
  campaignId: number,
  accessToken: string,
  signal?: string,
): Promise<CampaignReasonSuggestions> {
  return postJson<CampaignReasonSuggestions, { signal?: string }>(
    `${CAMPAIGNS}/${campaignId}/reason-suggestions`,
    signal ? { signal } : {},
    accessToken,
  );
}

type CampaignMutationResult = {
  result: Record<string, number>;
  campaign: CampaignDetail;
};

export function assignCampaignMainSignal(
  campaignId: number,
  mainSignal: string,
  selection: CampaignSelection,
  accessToken: string,
  previewPage = 1,
  filters: CampaignFilters = {},
): Promise<CampaignMutationResult> {
  const params = campaignPreviewParams(previewPage, filters);
  return postJson<CampaignMutationResult, { main_signal: string; selection: CampaignSelection }>(
    `${CAMPAIGNS}/${campaignId}/recipients/main-signal?${params.toString()}`,
    { main_signal: mainSignal, selection },
    accessToken,
  );
}

export function removeCampaignRecipients(
  campaignId: number,
  selection: CampaignSelection,
  accessToken: string,
  previewPage = 1,
  filters: CampaignFilters = {},
): Promise<CampaignMutationResult> {
  const params = campaignPreviewParams(previewPage, filters);
  return postJson<CampaignMutationResult, { selection: CampaignSelection }>(
    `${CAMPAIGNS}/${campaignId}/recipients/remove?${params.toString()}`,
    { selection },
    accessToken,
  );
}

export function sendCampaign(
  campaignId: number,
  accessToken: string,
): Promise<CampaignSendResult> {
  return postJson<CampaignSendResult, Record<string, never>>(
    `${CAMPAIGNS}/${campaignId}/send`,
    {},
    accessToken,
  );
}

/** Discards a draft; its leads go back to the pool unattached. */
export function deleteCampaign(
  campaignId: number,
  accessToken: string,
): Promise<{ deleted: number }> {
  return deleteJson<{ deleted: number }>(`${CAMPAIGNS}/${campaignId}`, accessToken);
}
