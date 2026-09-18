import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { IntegrationDataView } from "@/features/integrations/integration-data-view";

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  const { mode } = await searchParams;
  return (
    <PlatformAdminView>
      <IntegrationDataView initialMode={mode === "live" ? "live" : "sandbox"} />
    </PlatformAdminView>
  );
}
