import type { ReactNode } from "react";

import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";

/**
 * Console tabs: /platform-admin/{overview|organizations|users|feature-flags|
 * operations|support|audit-log|engineering}. The console lives in this layout
 * (the "(console)" group does not appear in URLs) so it stays mounted while the
 * tab path changes. Integrations keep their own pages under /platform-admin/integrations.
 */
export default function PlatformConsoleLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <PlatformAdminView />
      {children}
    </>
  );
}
