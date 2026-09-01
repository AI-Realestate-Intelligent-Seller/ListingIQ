/* Why Us — trust, control, reliability. Copy left, SVG right. */
import { LeadIntelligenceCard } from "../../data/svg-components/LeadIntelligenceCard";

export function WhyUsSection() {
  return (
    <section className="lp-why" id="why-listingiq">
      <div className="lp-why-inner">

        {/* Left — trust copy */}
        <div className="lp-why-copy">
          <p className="lp-eyebrow lp-eyebrow-dark">Why ListingIQ</p>
          <h2>Built to be trusted.<br />Built to be controlled.</h2>

          <div className="lp-why-principles">
            <div className="lp-why-item">
              <div className="lp-why-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <div>
                <h4>Your team spends time where it matters.</h4>
                <p>Agents step in when a lead is ready for a real conversation, instead of chasing every new inquiry.</p>
              </div>
            </div>

            

            <div className="lp-why-item">
              <div className="lp-why-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
              </div>
              <div>
                <h4>The handoff is final.</h4>
                <p>Once an agent takes over, AI stays quiet until the agent manually hands the conversation back.</p>
              </div>
            </div>
          </div>
        </div>

        {/* Right — lead intelligence card SVG */}
        <div className="lp-why-visual">
          <div className="lp-why-card">
            <LeadIntelligenceCard />
          </div>
        </div>

      </div>
    </section>
  );
}
