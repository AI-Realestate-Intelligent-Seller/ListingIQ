export type PlatformOverview = {
  organizations: number; active_organizations: number; users: number; active_users: number;
  leads: number; campaigns: number; messages: number; new_users_30d: number; pending_invitations: number;
};
export type Organization = { id: string; name: string; owner_email: string | null; user_count: number;
  active_user_count: number; lead_count: number; is_active: boolean; created_at: string | null;
  onboarding_status: "invited" | "onboarded" };
export type BrokerageOnboardingResponse = { message: string; brokerage_id: string; expires_at: string };
export type PlatformUser = { id: number; email: string; full_name: string | null; role: string;
  brokerage_id: string | null; brokerage_name: string | null; is_active: boolean; is_verified: boolean; created_at: string | null };
export type FeatureFlag = { id: number; key: string; description: string | null; enabled: boolean;
  brokerage_id: string | null; updated_at: string | null };
export type Operations = { pending_invitations: number; draft_campaigns: number; queued_conversations: number;
  due_followups: number; failed_messages: number; ai_runs_24h: number };
export type AuditEvent = { id: number; actor_email: string; action: string; target_type: string;
  target_id: string | null; detail: string | null; created_at: string | null };
export type Engineering = { environment: string; release: string; commit: string; python: string; database: string; sms_mode: string };
