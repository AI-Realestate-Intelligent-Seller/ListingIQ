export type AuthMode = "login" | "register";

export type UserRole = "hob" | "broker" | "agent";

export type AuthUser = {
  id: number;
  email: string;
  first_name: string;
  last_name: string;
  full_name: string;
  brokerage_id: string;
  brokerage_name: string;
  role: UserRole;
  is_verified: boolean;
  is_active: boolean;
  created_at: string;
};

export type AuthResponse = {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
};

export type LoginPayload = {
  email: string;
  password: string;
};

export type RegisterPayload = LoginPayload & {
  first_name: string;
  last_name: string;
  brokerage_name: string;
  is_head_or_owner: boolean;
};

export type RegistrationResponse = {
  message: string;
  email: string;
};

export type MessageResponse = {
  message: string;
};

export type InvitableRole = Exclude<UserRole, "hob">;

/** Response of POST /team/invitations — deliberately free of the raw token. */
export type InvitationCreatedResponse = {
  message: string;
};

export type DirectoryMember = {
  id: number;
  full_name: string | null;
  email: string;
  role: InvitableRole;
  is_active: boolean;
  assigned_broker_id: number | null;
};

export type TeamDirectoryResponse = {
  members: DirectoryMember[];
};

export type BrokerageOverview = {
  total_leads: number;
  campaigns_sent_this_month: number;
  campaign_conversations: number;
  campaign_replied: number;
  campaign_reply_rate: number;
  replies_requiring_attention: number;
  appointments_booked_this_month: number;
  booked_leads: number;
  lead_to_appointment_rate: number;
  average_response_seconds: number | null;
  workload: {
    user_id: number;
    name: string;
    role: InvitableRole;
    lead_count: number;
  }[];
};

/** Response of GET /team/invitations/{token}. */
export type InvitationValidation = {
  valid: boolean;
  email: string | null;
  role: InvitableRole | null;
  role_label: string | null;
  brokerage_name: string | null;
  expires_at: string | null;
  message: string | null;
};

/** Response of POST /team/invitations/{token}/accept — a ready-to-use session. */
export type InvitationAcceptResponse = AuthResponse & {
  message: string;
};
