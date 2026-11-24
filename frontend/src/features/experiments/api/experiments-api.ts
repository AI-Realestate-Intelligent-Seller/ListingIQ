import { getJson, patchJson, postJson } from "@/lib/api/http-client";

import type { Experiment, ExperimentCreateInput, ExperimentFollowupUpdate } from "../types/experiments.types";

const EXPERIMENTS = "/experiments";

export function listExperiments(token: string): Promise<Experiment[]> {
  return getJson<Experiment[]>(EXPERIMENTS, token);
}

export function createExperiment(input: ExperimentCreateInput, token: string): Promise<Experiment> {
  return postJson<Experiment, ExperimentCreateInput>(EXPERIMENTS, input, token);
}

export function cancelExperiment(id: number, token: string): Promise<Experiment> {
  return postJson<Experiment, Record<string, never>>(`${EXPERIMENTS}/${id}/cancel`, {}, token);
}

export function updateExperimentFollowup(
  experimentId: number,
  campaignId: number,
  input: ExperimentFollowupUpdate,
  token: string,
): Promise<Experiment> {
  return patchJson<Experiment, ExperimentFollowupUpdate>(
    `${EXPERIMENTS}/${experimentId}/followups/${campaignId}`,
    input,
    token,
  );
}

export function addExperimentRandomLeads(
  experimentId: number,
  campaignId: number,
  count: number,
  token: string,
): Promise<Experiment> {
  return postJson<Experiment, { count: number }>(
    `${EXPERIMENTS}/${experimentId}/variants/${campaignId}/random-leads`,
    { count },
    token,
  );
}

export function renameExperiment(id: number, name: string, token: string): Promise<Experiment> {
  return patchJson<Experiment, { name: string }>(`${EXPERIMENTS}/${id}`, { name }, token);
}
