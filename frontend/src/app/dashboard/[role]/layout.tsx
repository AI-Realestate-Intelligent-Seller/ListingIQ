import { Suspense, type ReactNode } from "react";

import { DashboardView } from "@/features/dashboard/components/dashboard-view";

/**
 * Role-scoped dashboard: /dashboard/hob, /dashboard/broker, /dashboard/agent.
 *
 * The dashboard lives in the layout, not the page, so it stays mounted while
 * the section path below it changes: switching sections, or going back and
 * forward, never reloads the shell, its data, or the realtime connection.
 */
export default function RoleDashboardLayout({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={null}>
      <DashboardView />
      {children}
    </Suspense>
  );
}
