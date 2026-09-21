import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { PropertyRadarView } from "@/features/integrations/propertyradar-view";
export default function Page() {
  return (
    <PlatformAdminView>
      <PropertyRadarView detail />
    </PlatformAdminView>
  );
}
