import { getJson, patchJson, postJson } from "@/lib/api/http-client";
import type { AgentOverview, AssignmentLead, AssignmentStage, AssignmentsResponse, BrokerOverview, BrokerSummary, MyAssignedLeadsResponse } from "../types/assignments.types";

export function listAssignments(token: string, signal?: AbortSignal): Promise<AssignmentsResponse> {
  return getJson<AssignmentsResponse>("/assignments", token, signal);
}

export function assignLead(leadId: number, agentId: number | null, token: string): Promise<AssignmentLead> {
  return patchJson<AssignmentLead, { agent_id: number | null }>(
    `/assignments/${leadId}`,
    { agent_id: agentId },
    token,
  );
}

export function roundRobinAssignments(token: string): Promise<AssignmentsResponse & { assigned: number }> {
  return postJson<AssignmentsResponse & { assigned: number }, Record<string, never>>(
    "/assignments/round-robin",
    {},
    token,
  );
}

export function listMyAssignedLeads(token: string, signal?: AbortSignal): Promise<MyAssignedLeadsResponse> {
  return getJson<MyAssignedLeadsResponse>("/assignments/mine", token, signal);
}

export function getAgentOverview(token: string, signal?: AbortSignal): Promise<AgentOverview> {
  return getJson<AgentOverview>("/assignments/mine/overview", token, signal);
}

export function getBrokerOverview(token: string, signal?: AbortSignal): Promise<BrokerOverview> {
  return getJson<BrokerOverview>("/assignments/overview", token, signal);
}

export function updateMyLeadStage(leadId: number, stage: AssignmentStage, token: string): Promise<AssignmentLead> {
  return patchJson<AssignmentLead, { stage: AssignmentStage }>(`/assignments/mine/${leadId}/stage`, { stage }, token);
}

export function getMyBroker(token: string, signal?: AbortSignal): Promise<{ broker: BrokerSummary | null }> {
  return getJson<{ broker: BrokerSummary | null }>("/assignments/my-broker", token, signal);
}
