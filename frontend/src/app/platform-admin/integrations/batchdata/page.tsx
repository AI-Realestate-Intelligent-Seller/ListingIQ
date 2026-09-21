import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { BatchDataView } from "@/features/integrations/batchdata-view";

export default function Page() {
  return <PlatformAdminView><BatchDataView detail /></PlatformAdminView>;
}
