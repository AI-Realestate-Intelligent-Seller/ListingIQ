import { Suspense } from "react";

import { AuthForm } from "@/features/auth/components/auth-form";

export default function LoginPage() {
  // AuthForm reads search params on the client, so it needs a suspense boundary.
  return (
    <Suspense fallback={<main className="auth-page" />}>
      <AuthForm mode="login" />
    </Suspense>
  );
}
