import { Suspense } from "react";

import { JoinInvitationView } from "@/features/auth/components/join-invitation-view";

export const metadata = {
  title: "Accept your invitation — ListingIQ",
};

export default function JoinPage() {
  return (
    <Suspense fallback={<main className="simple-auth-page" />}>
      <JoinInvitationView />
    </Suspense>
  );
}
