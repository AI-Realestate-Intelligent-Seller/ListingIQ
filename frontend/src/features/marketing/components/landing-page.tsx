import { FinalCtaSection } from "./sections/final-cta-section";
import { HeroSection } from "./sections/hero-section";
import { ProductFeature } from "./sections/product-feature";
import { TeamSection } from "./sections/team-section";
import { WhyUsSection } from "./sections/why-us-section";
import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";
import { VoiceSection } from "./sections/voice-section";


export function LandingPage() {
  return (
    <main>
      <SiteHeader />
      <HeroSection />
      <ProductFeature />
      <TeamSection />
      <VoiceSection />
      <WhyUsSection />
      <FinalCtaSection />
      <SiteFooter />
    </main>
  );
}
