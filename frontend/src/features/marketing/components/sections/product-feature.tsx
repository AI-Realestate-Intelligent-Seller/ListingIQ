/* Features / platform overview — copy left, SVG right. */
import { ProductDashboard } from "../../data/svg-components/ProductDashboard";

export function ProductFeature() {
  return (
    <section className="lp-features lp-ecosystem" id="features">
      <div className="lp-features-inner">

        <div className="lp-ecosystem-copy">
          <p className="lp-eyebrow lp-eyebrow-dark">The ListingIQ Platform</p>
          <h2>Everything your team needs.<br />In one place.</h2>
          <p>
            Leads, conversations, calls, and calendars  one dashboard, not multiple tools stitched together. AI replies by SMS in your agent&apos;s voice, enriched with context on every lead, and hands off the moment a seller&apos;s ready to talk. Calls sync to your calendar automatically. Every lead arrives scored, tagged, and assigned. Brokers, agents, and HOBs each see exactly their view nothing scattered, nothing dropped.
          </p>
        </div>

        <div className="lp-ecosystem-visual">
          <ProductDashboard />
        </div>

      </div>
    </section>
  );
}
