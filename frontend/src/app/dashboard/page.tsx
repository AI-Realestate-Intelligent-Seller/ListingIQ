import { Suspense } from "react";

import { DashboardView } from "@/features/dashboard/components/dashboard-view";

/** Plain /dashboard (push links, old bookmarks) moves to /dashboard/{role}, keeping the query. */
export default function DashboardPage() {
  return (
    <Suspense fallback={null}>
      <DashboardView />
    </Suspense>
  );
}
