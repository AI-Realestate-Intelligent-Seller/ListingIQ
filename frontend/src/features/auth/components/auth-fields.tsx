import Link from "next/link";

import { ArrowIcon } from "@/components/icons/arrow-icon";

import { AUTH_COPY } from "../constants/auth-copy";
import type { AuthMode } from "../types/auth.types";

type AuthFieldsProps = {
  mode: AuthMode;
  errorMessage: string;
  isSubmitting: boolean;
  showPassword: boolean;
  onSubmit: React.FormEventHandler<HTMLFormElement>;
  onTogglePassword: () => void;
};

export function AuthFields({
  mode,
  errorMessage,
  isSubmitting,
  showPassword,
  onSubmit,
  onTogglePassword,
}: AuthFieldsProps) {
  const isRegister = mode === "register";
  const copy = AUTH_COPY[mode];

  return (
    <form className="auth-fields" onSubmit={onSubmit}>
      {isRegister ? (
        <div className="field-row">
          <label htmlFor="firstName">
            <span>First name</span>
            <input
              id="firstName"
              name="firstName"
              autoComplete="given-name"
              placeholder="Mir"
              required
            />
          </label>

          <label htmlFor="lastName">
            <span>Last name</span>
            <input
              id="lastName"
              name="lastName"
              autoComplete="family-name"
              placeholder="Ali"
              required
            />
          </label>
        </div>
      ) : null}

      {isRegister ? (
        <label htmlFor="brokerage">
          <span>Brokerage name</span>
          <input
            id="brokerage"
            name="brokerage"
            autoComplete="organization"
            placeholder="Your brokerage"
            required
          />
        </label>
      ) : null}

      <label htmlFor="email">
        <span>Work email</span>
        <input
          id="email"
          type="email"
          name="email"
          autoComplete="email"
          placeholder="you@brokerage.com"
          required
        />
      </label>

      <label htmlFor="password">
        <span className="label-line">
          Password
          {!isRegister ? (
            <Link href="/forgot-password">Forgot password?</Link>
          ) : null}
        </span>

        <div className="password-field">
          <input
            id="password"
            type={showPassword ? "text" : "password"}
            name="password"
            autoComplete={isRegister ? "new-password" : "current-password"}
            minLength={8}
            placeholder={
              isRegister ? "At least 8 characters" : "Enter your password"
            }
            required
          />
          <button
            type="button"
            aria-controls="password"
            aria-pressed={showPassword}
            onClick={onTogglePassword}
          >
            {showPassword ? "Hide" : "Show"}
          </button>
        </div>
      </label>

      {isRegister ? (
        <label className="terms-check hob-confirmation">
          <input type="checkbox" name="isHeadOrOwner" required />
          <span>
            I confirm that I am the Head or Owner of this brokerage and should
            be assigned the HOB role.
          </span>
        </label>
      ) : null}

      {isRegister ? (
        <label className="terms-check">
          <input type="checkbox" required />
          <span>
            I agree to the <a href="#">Terms</a> and{" "}
            <a href="#">Privacy Policy</a>.
          </span>
        </label>
      ) : (
        <label className="terms-check">
          <input type="checkbox" name="remember" />
          <span>Keep me signed in</span>
        </label>
      )}

      {errorMessage ? (
        <div className="auth-error" role="alert" aria-live="polite">
          {errorMessage}
        </div>
      ) : null}

      <button
        className="button auth-submit"
        type="submit"
        disabled={isSubmitting}
      >
        {isSubmitting ? "Please wait…" : copy.submitLabel}
        {!isSubmitting ? <ArrowIcon /> : null}
      </button>
    </form>
  );
}
