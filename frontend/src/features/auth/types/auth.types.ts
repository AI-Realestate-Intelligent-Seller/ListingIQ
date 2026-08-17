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

export type InvitableRole = Exclude<UserRole, "hob">;

/** Response of POST /team/invitations — deliberately free of the raw token. */
export type InvitationCreatedResponse = {
  message: string;
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
