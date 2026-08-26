"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import { Brand } from "@/components/brand/brand";
import { ArrowIcon } from "@/components/icons/arrow-icon";
import { resetPassword } from "../api/auth-api";

export function ResetPasswordView() {
  const token = useSearchParams().get("token") ?? "";
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState(token ? "" : "This password reset link is invalid or has expired.");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirmation = String(form.get("confirmation") ?? "");
    setError("");
    if (password !== confirmation) {
      setError("Passwords do not match.");
      return;
    }
    setIsSubmitting(true);
    try {
      const response = await resetPassword(token, password);
      setMessage(response.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not reset your password.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="simple-auth-page">
      <Brand />
      <div className="simple-auth-card">
        <span className="auth-eyebrow">ACCOUNT RECOVERY</span>
        <h1>Choose a new password.</h1>
        <p>Use at least eight characters. This reset link can only be used once.</p>
        {message ? (
          <>
            <div className="auth-notice" role="status">{message}</div>
            <Link href="/login">Continue to log in →</Link>
          </>
        ) : (
          <form onSubmit={submit}>
            <label htmlFor="newPassword"><span>New password</span><input id="newPassword" name="password" type="password" autoComplete="new-password" minLength={8} required /></label>
            <label htmlFor="confirmPassword"><span>Confirm password</span><input id="confirmPassword" name="confirmation" type="password" autoComplete="new-password" minLength={8} required /></label>
            <button className="button auth-submit" type="submit" disabled={isSubmitting || !token}>{isSubmitting ? "Resetting…" : "Reset password"}<ArrowIcon /></button>
            {error ? <p className="auth-error" role="alert">{error}</p> : null}
          </form>
        )}
        {!message ? <Link href="/login">← Back to log in</Link> : null}
      </div>
    </main>
  );
}
