"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Brand } from "@/components/brand/brand";
import { getJson } from "@/lib/api/http-client";

export function VerifyEmailView() {
  const token = useSearchParams().get("token");
  const [message, setMessage] = useState("Verifying your email…");
  const [verified, setVerified] = useState(false);

  useEffect(() => {
    if (!token) {
      // Search parameters are supplied by the browser after this client page mounts.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMessage("This verification link is missing its token.");
      return;
    }
    getJson<{ message: string }>(`/auth/verify-email?token=${encodeURIComponent(token)}`)
      .then((response) => {
        setVerified(true);
        setMessage(response.message);
      })
      .catch((error: unknown) => setMessage(error instanceof Error ? error.message : "Verification failed"));
  }, [token]);

  return (
    <main className="simple-auth-page">
      <Brand />
      <section className="simple-auth-card auth-success" role="status">
        <h1>{verified ? "Email verified" : "Email verification"}</h1>
        <p>{message}</p>
        {verified ? <Link className="button" href="/login">Continue to login</Link> : null}
      </section>
    </main>
  );
}
