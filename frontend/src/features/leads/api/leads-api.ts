import { getJson, postFile, postForm, postJson } from "@/lib/api/http-client";

import type {
  CampaignResult,
  ImportPreview,
  LeadDetail,
  LeadImportResult,
  LeadPoolResponse,
  LeadQuery,
} from "../types/leads.types";

const LEADS = "/leads";

export function fetchLeadPool(
  query: LeadQuery,
  accessToken: string,
  signal?: AbortSignal,
): Promise<LeadPoolResponse> {
  const params = new URLSearchParams();
  if (query.search) params.set("search", query.search);
  if (query.stage) params.set("stage", query.stage);
  // Repeated keys — the API requires every listed signal to be present.
  for (const key of query.signals ?? []) params.append("signal", key);

  const suffix = params.toString();
  return getJson<LeadPoolResponse>(`${LEADS}${suffix ? `?${suffix}` : ""}`, accessToken, signal);
}

/** One lead in full, for the details drawer. */
export function fetchLead(
  leadId: number,
  accessToken: string,
  signal?: AbortSignal,
): Promise<LeadDetail> {
  return getJson<LeadDetail>(`${LEADS}/${leadId}`, accessToken, signal);
}

/** Dry run: what the file would import, so the options can show real numbers. */
export function previewImport(file: File, accessToken: string): Promise<ImportPreview> {
  return postFile<ImportPreview>(`${LEADS}/preview`, file, accessToken);
}

/** Re-run the dry run on the staged file with a different column mapping. */
export function repreviewImport(
  token: string,
  mapping: Record<string, string>,
  accessToken: string,
): Promise<ImportPreview> {
  return postForm<ImportPreview>(
    `${LEADS}/preview`,
    { token, mapping: JSON.stringify(mapping) },
    accessToken,
  );
}

/**
 * Imports the file the preview already staged, identified by its token — a
 * large spreadsheet is never uploaded twice.
 */
export function importLeads(
  token: string,
  options: { limit: number; mapping: Record<string, string> },
  accessToken: string,
): Promise<LeadImportResult> {
  return postForm<LeadImportResult>(
    `${LEADS}/import`,
    { token, limit: String(options.limit), mapping: JSON.stringify(options.mapping) },
    accessToken,
  );
}

/** Bulk removal. POST, not DELETE: the selection travels in the body. */
export function deleteLeads(leadIds: number[], accessToken: string): Promise<{ deleted: number }> {
  return postJson<{ deleted: number }, { lead_ids: number[] }>(
    `${LEADS}/delete`,
    { lead_ids: leadIds },
    accessToken,
  );
}

export function startCampaign(
  leadIds: number[],
  outreachReason: string,
  accessToken: string,
): Promise<CampaignResult> {
  return postJson<CampaignResult, { lead_ids: number[]; outreach_reason: string | null }>(
    `${LEADS}/campaign`,
    { lead_ids: leadIds, outreach_reason: outreachReason.trim() || null },
    accessToken,
  );
}
