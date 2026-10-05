import type { ReactNode } from "react";

export const integrationSections = [
  "Connection",
  "Configuration",
  "Data",
  "Usage & Limits",
  "Activity",
  "History",
] as const;

export type IntegrationSection = (typeof integrationSections)[number];
export type IntegrationSubtab = { id: string; label: string };

export function IntegrationNavigation({
  provider,
  activeSection,
  onSectionChange,
  subtabs,
  activeSubtab,
  onSubtabChange,
  help,
  sections = integrationSections,
}: {
  provider: string;
  activeSection: IntegrationSection;
  onSectionChange: (section: IntegrationSection) => void;
  subtabs: readonly IntegrationSubtab[];
  activeSubtab: string;
  onSubtabChange: (subtab: string) => void;
  help?: ReactNode;
  sections?: readonly IntegrationSection[];
}) {
  return (
    <div className="integration-navigation">
      <div className="pr-tab-row">
        <nav
          className="pr-tabs integration-main-tabs"
          aria-label={`${provider} sections`}
        >
          {sections.map((section) => (
            <button
              key={section}
              className={activeSection === section ? "active" : ""}
              aria-current={activeSection === section ? "page" : undefined}
              onClick={() => onSectionChange(section)}
            >
              {section}
            </button>
          ))}
        </nav>
        {help}
      </div>
      {subtabs.length > 1 && (
        <nav
          className="integration-subtabs"
          aria-label={`${provider} ${activeSection} tools`}
        >
          {subtabs.map((subtab) => (
            <button
              key={subtab.id}
              className={activeSubtab === subtab.id ? "active" : ""}
              aria-current={activeSubtab === subtab.id ? "page" : undefined}
              onClick={() => onSubtabChange(subtab.id)}
            >
              {subtab.label}
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}
