import { PlatformAdminView } from "@/features/platform-admin/components/platform-admin-view";
import { PropertyRadarView } from "@/features/integrations/propertyradar-view";
import { BatchDataView } from "@/features/integrations/batchdata-view";
import { DealMachineView } from "@/features/integrations/dealmachine-view";
import { IntegrationWorkspaceNav } from "@/features/integrations/integration-data-view";
export default function Page() {
  return (
    <PlatformAdminView>
      <div className="integration-index">
        <header>
          <span>OPERATIONS / INTEGRATIONS</span>
          <h1>Integrations</h1>
          <p>
            Provider connections, paid usage, monitoring and operational
            history.
          </p>
        </header>
        <IntegrationWorkspaceNav />
        <div className="integration-grid">
          <PropertyRadarView />
          <BatchDataView />
          <DealMachineView />
        </div>
      </div>
    </PlatformAdminView>
  );
}
