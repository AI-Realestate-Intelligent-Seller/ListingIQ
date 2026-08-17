import { CapabilitiesSection } from "./sections/capabilities-section";
import { FinalCtaSection } from "./sections/final-cta-section";
import { HeroSection } from "./sections/hero-section";
import { HumanHandoffSection } from "./sections/human-handoff-section";
import { RolesSection } from "./sections/roles-section";
import { SecuritySection } from "./sections/security-section";
import { SignalsSection } from "./sections/signals-section";
import { WorkflowSection } from "./sections/workflow-section";
import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";

export function LandingPage() {
  return (
    <main>
      <SiteHeader />
      <HeroSection />

      <section className="credibility" aria-label="Product strengths">
        <span>Unified seller intelligence</span>
        <i />
        <span>Voice · SMS · Email</span>
        <i />
        <span>Role-based workflows</span>
        <i />
        <span>CRM-ready</span>
      </section>

      <CapabilitiesSection />
      <WorkflowSection />
      <SignalsSection />
      <HumanHandoffSection />
      <RolesSection />
      <SecuritySection />
      <FinalCtaSection />
      <SiteFooter />
    </main>
  );
}
