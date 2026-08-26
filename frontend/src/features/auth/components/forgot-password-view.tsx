"use client";

import Link from "next/link";
import { useState } from "react";

import { Brand } from "@/components/brand/brand";
import { ArrowIcon } from "@/components/icons/arrow-icon";
import { requestPasswordReset } from "../api/auth-api";

export function ForgotPasswordView() {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage("");
    setError("");
    setIsSubmitting(true);
    const email = String(new FormData(event.currentTarget).get("email") ?? "").trim();
    try {
      const response = await requestPasswordReset(email);
      setMessage(response.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "We could not request a reset link.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="simple-auth-page">
      <Brand />

      <div className="simple-auth-card">
        <span className="auth-eyebrow">ACCOUNT RECOVERY</span>
        <h1>Reset your password.</h1>
        <p>Enter your work email and we’ll send you a secure reset link.</p>

        <form onSubmit={submit}>
          <label htmlFor="recoveryEmail">
            <span>Work email</span>
            <input
              id="recoveryEmail"
              name="email"
              type="email"
              autoComplete="email"
              placeholder="you@brokerage.com"
              required
            />
          </label>
          <button className="button auth-submit" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Sending…" : "Send reset link"}
            <ArrowIcon />
          </button>
          {message ? <div className="auth-notice" role="status">{message}</div> : null}
          {error ? <p className="auth-error" role="alert">{error}</p> : null}
        </form>

        <Link href="/login">← Back to log in</Link>
      </div>
    </main>
  );
}
