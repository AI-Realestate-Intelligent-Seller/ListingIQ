import type { Lead } from "@/features/leads/types/leads.types";

export type AssignmentAgent = { id: number; full_name: string | null; email: string };
export type AssignmentStage =
  | "new"
  | "processing"
  | "want_more_info"
  | "interested"
  | "ready_to_sell"
  | "not_interested"
  | "no_response"
  | "dnc";
export type AssignmentLead = Lead & {
  assignee_id: number | null;
  assignment_stage: AssignmentStage;
  assignment_stage_changed_at: string | null;
  stage_seconds: Record<AssignmentStage, number>;
  campaign_id: number | null;
  campaign_name: string;
};
export type AssignmentsResponse = { leads: AssignmentLead[]; agents: AssignmentAgent[] };
export type BrokerSummary = { id: number; full_name: string | null; email: string };
export type MyAssignedLeadsResponse = { leads: AssignmentLead[]; broker: BrokerSummary | null };
export type UpcomingEvent = {
  id: number;
  name: string | null;
  title: string | null;
  start_at: string;
  end_at: string | null;
};
export type AgentOverview = {
  assigned_leads: number;
  new_assignments: number;
  in_progress_leads: number;
  completed_leads: number;
  replies_requiring_attention: number;
  appointments_booked: number;
  completion_rate: number;
  average_handling_seconds: number | null;
  average_response_seconds: number | null;
  reply_rate: number | null;
upcoming_events:UpcomingEvent[];
};
export type BrokerOverview = {
  campaigns_started: number;
  campaigns_completed: number;
  campaigns_in_draft: number;
  campaign_to_matured_rate: number;
  total_assigned: number;
  total_in_progress: number;
  total_completed: number;
  total_booked: number;
};
