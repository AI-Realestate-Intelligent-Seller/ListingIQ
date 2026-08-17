import { Suspense } from "react";

import { VerifyEmailView } from "@/features/auth/components/verify-email-view";

export default function VerifyEmailPage() {
  // The view reads the token from search params on the client.
  return (
    <Suspense fallback={<main className="simple-auth-page" />}>
      <VerifyEmailView />
    </Suspense>
  );
}
