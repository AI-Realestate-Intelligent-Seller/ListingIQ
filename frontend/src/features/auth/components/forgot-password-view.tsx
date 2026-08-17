import Link from "next/link";

import { Brand } from "@/components/brand/brand";
import { ArrowIcon } from "@/components/icons/arrow-icon";

export function ForgotPasswordView() {
  return (
    <main className="simple-auth-page">
      <Brand />

      <div className="simple-auth-card">
        <span className="auth-eyebrow">ACCOUNT RECOVERY</span>
        <h1>Reset your password.</h1>
        <p>Enter your work email and we’ll send you a secure reset link.</p>

        <form>
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
          <button className="button auth-submit" type="submit">
            Send reset link
            <ArrowIcon />
          </button>
        </form>

        <Link href="/login">← Back to log in</Link>
      </div>
    </main>
  );
}
