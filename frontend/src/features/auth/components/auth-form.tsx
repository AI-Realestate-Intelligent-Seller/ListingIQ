"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Brand } from "@/components/brand/brand";

import { login, register } from "../api/auth-api";
import { AUTH_COPY } from "../constants/auth-copy";
import { saveAuthSession } from "../lib/auth-storage";
import type {
  AuthMode,
  LoginPayload,
  RegisterPayload,
} from "../types/auth.types";
import { AuthFields } from "./auth-fields";
import { AuthVisual } from "./auth-visual";

type AuthFormProps = {
  mode: AuthMode;
};

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isRegister = mode === "register";
  const copy = AUTH_COPY[mode];
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    setErrorMessage("");
    setIsSubmitting(true);

    const formData = new FormData(event.currentTarget);

    try {
      if (isRegister) {
        await register(buildRegisterPayload(formData));
        router.replace("/login?registered=1");
        return;
      }

      const auth = await login(buildLoginPayload(formData));
      const keepSignedIn = formData.get("remember") === "on";
      saveAuthSession(auth, keepSignedIn ? "local" : "session");
      router.replace(`/dashboard/${auth.user.role}`);
    } catch (error) {
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Authentication failed. Please try again.",
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <AuthVisual mode={mode} />

      <section className="auth-panel">
        <div className="auth-mobile-brand">
          <Brand />
        </div>

        <Link className="back-home" href="/">
          ← Back to ListingIQ
        </Link>

        <div className="auth-form-wrap">
          <span className="auth-eyebrow">{copy.eyebrow}</span>
          <h1>{copy.title}</h1>
          <p>{copy.description}</p>
          {!isRegister && searchParams.get("registered") === "1" ? (
            <div className="auth-notice" role="status">
              Account created. Check your business email and verify it before logging in.
            </div>
          ) : null}
          {!isRegister && searchParams.get("expired") === "1" ? (
            <div className="auth-notice" role="status">
              Your session ended after 8 hours of inactivity. Please log in again.
            </div>
          ) : null}

          <AuthFields
            mode={mode}
            errorMessage={errorMessage}
            isSubmitting={isSubmitting}
            showPassword={showPassword}
            onSubmit={handleSubmit}
            onTogglePassword={() => setShowPassword((visible) => !visible)}
          />

          <div className="auth-switch">
            {isRegister ? "Already have an account?" : "New to ListingIQ?"}{" "}
            <Link href={isRegister ? "/login" : "/register"}>
              {isRegister ? "Log in" : "Create an account"}
            </Link>
          </div>

          {isRegister ? (
            <div className="register-note">
              <span>✓</span> No credit card required <i /> <span>✓</span> Guided
              onboarding
            </div>
          ) : null}
        </div>

        <div className="auth-footer">
          <span>© 2026 ListingIQ</span>
          <a href="mailto:hello@ListingIQ.ai">Need help?</a>
        </div>
      </section>
    </main>
  );
}

function buildLoginPayload(formData: FormData): LoginPayload {
  const email = getRequiredFormValue(formData, "email");
  validateBusinessEmail(email);

  return {
    email,
    password: getRequiredFormValue(formData, "password"),
  };
}

function validateBusinessEmail(email: string): void {
  const domain = email.split("@").pop()?.toLowerCase();
  const isPersonalEmail =
    domain === "gmail.com" ||
    domain === "googlemail.com" ||
    domain === "yahoo.com" ||
    domain?.startsWith("yahoo.");

  if (isPersonalEmail) {
    throw new Error(
      "Please use your business email address.",
    );
  }
}

function buildRegisterPayload(formData: FormData): RegisterPayload {
  return {
    ...buildLoginPayload(formData),
    first_name: getRequiredFormValue(formData, "firstName"),
    last_name: getRequiredFormValue(formData, "lastName"),
    brokerage_name: getRequiredFormValue(formData, "brokerage"),
    is_head_or_owner: formData.get("isHeadOrOwner") === "on",
  };
}

function getRequiredFormValue(formData: FormData, fieldName: string): string {
  const value = formData.get(fieldName);

  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`The ${fieldName} field is required.`);
  }

  return value.trim();
}
