import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { DealMachineView } from "@/features/integrations/dealmachine-view";

export default function Page() {
  return <PlatformAdminView><DealMachineView detail /></PlatformAdminView>;
}
