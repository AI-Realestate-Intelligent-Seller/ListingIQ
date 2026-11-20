import { getJson, postJson } from "@/lib/api/http-client";

import type { Experiment, ExperimentCreateInput } from "../types/experiments.types";

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
