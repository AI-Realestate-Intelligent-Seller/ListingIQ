import { Suspense } from "react";
import { ResetPasswordView } from "@/features/auth/components/reset-password-view";

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordView />
    </Suspense>
  );
}