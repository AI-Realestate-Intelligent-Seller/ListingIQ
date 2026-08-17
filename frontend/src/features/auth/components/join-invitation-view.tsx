"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/brand/brand";

import { acceptInvitation, getInvitation } from "../api/auth-api";
import { saveAuthSession } from "../lib/auth-storage";
import type { InvitationValidation } from "../types/auth.types";

const INVALID_INVITATION_MESSAGE = "This invitation is invalid or has expired.";

type Status = "checking" | "invalid" | "ready" | "accepted";

export function JoinInvitationView() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";

  const [status, setStatus] = useState<Status>("checking");
  const [invitation, setInvitation] = useState<InvitationValidation | null>(null);
  const [message, setMessage] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    let active = true;

    if (!token) {
      // Search parameters are supplied by the browser after this client page mounts.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setStatus("invalid");
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMessage("This invitation link is missing its token.");
      return;
    }

    getInvitation(token)
      .then((response) => {
        if (!active) return;
        if (!response.valid) {
          setStatus("invalid");
          setMessage(response.message ?? INVALID_INVITATION_MESSAGE);
          return;
        }
        setInvitation(response);
        setStatus("ready");
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setStatus("invalid");
        setMessage(
          reason instanceof Error ? reason.message : INVALID_INVITATION_MESSAGE,
        );
      });

    return () => {
      active = false;
    };
  }, [token]);

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (isSubmitting) return;

    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");

    setErrorMessage("");

    if (password !== String(form.get("confirmPassword") ?? "")) {
      setErrorMessage("Both passwords must match.");
      return;
    }

    setIsSubmitting(true);
    try {
      // Role and brokerage are never sent — the backend takes them from the invitation.
      const auth = await acceptInvitation(token, {
        first_name: String(form.get("firstName") ?? "").trim(),
        last_name: String(form.get("lastName") ?? "").trim(),
        password,
      });

      saveAuthSession(auth, "local");
      setMessage(auth.message);
      setStatus("accepted");
      router.replace(`/dashboard/${auth.user.role}`);
    } catch (reason) {
      setErrorMessage(
        reason instanceof Error
          ? reason.message
          : "We could not complete your registration. Please try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="simple-auth-page">
      <Brand />
      <section className="simple-auth-card join-card">
        {status === "checking" ? <p>Checking your invitation…</p> : null}

        {status === "invalid" ? (
          <>
            <span className="auth-eyebrow">TEAM INVITATION</span>
            <h1>Invitation unavailable</h1>
            <div className="auth-error" role="alert">
              {message || INVALID_INVITATION_MESSAGE}
            </div>
            <p>Ask your Head of Brokerage to send you a new invitation.</p>
          </>
        ) : null}

        {status === "accepted" ? (
          <>
            <h1>Account created successfully</h1>
            <p role="status">{message}</p>
          </>
        ) : null}

        {status === "ready" && invitation ? (
          <>
            <span className="auth-eyebrow">TEAM INVITATION</span>
            <h1>You&apos;re invited to join {invitation.brokerage_name}</h1>
            <p>
              Complete your registration to activate your ListingIQ account.
            </p>

            <dl className="invite-summary">
              <div>
                <dt>Role</dt>
                {/* The role is assigned by the Head of Brokerage and cannot be changed. */}
                <dd>{invitation.role_label ?? invitation.role}</dd>
              </div>
              <div>
                <dt>Email</dt>
                <dd>{invitation.email}</dd>
              </div>
            </dl>

            <form onSubmit={handleSubmit}>
              <div className="field-row">
                <input
                  name="firstName"
                  aria-label="First name"
                  placeholder="First name"
                  autoComplete="given-name"
                  disabled={isSubmitting}
                  required
                />
                <input
                  name="lastName"
                  aria-label="Last name"
                  placeholder="Last name"
                  autoComplete="family-name"
                  disabled={isSubmitting}
                  required
                />
              </div>
              <input
                name="password"
                type="password"
                aria-label="Password"
                placeholder="Create password (8+ characters)"
                autoComplete="new-password"
                minLength={8}
                disabled={isSubmitting}
                required
              />
              <input
                name="confirmPassword"
                type="password"
                aria-label="Confirm password"
                placeholder="Confirm password"
                autoComplete="new-password"
                minLength={8}
                disabled={isSubmitting}
                required
              />
              {errorMessage ? (
                <div className="auth-error" role="alert">{errorMessage}</div>
              ) : null}
              <button className="button" type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating your account…" : "Accept Invitation & Create Account"}
              </button>
            </form>
          </>
        ) : null}

        <Link href="/login">Already registered? Log in</Link>
      </section>
    </main>
  );
}
