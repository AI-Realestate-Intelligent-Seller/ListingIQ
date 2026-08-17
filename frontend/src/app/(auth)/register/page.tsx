import { Suspense } from "react";

import { AuthForm } from "@/features/auth/components/auth-form";

export default function RegisterPage() {
  // AuthForm reads search params on the client, so it needs a suspense boundary.
  return (
    <Suspense fallback={<main className="auth-page" />}>
      <AuthForm mode="register" />
    </Suspense>
  );
}
