import { SELLER_SIGNALS } from "../../data/landing-page-data";
import { SectionLabel } from "../section-label";

export function SignalsSection() {
  return (
    <section className="signals section">
      <div className="signals-copy">
        <SectionLabel number="03">Signal intelligence</SectionLabel>
        <h2>
          See motivation
          <br />
          <em>before it becomes obvious.</em>
        </h2>
        <p>
          ListingIQ combines multiple property and owner signals into one
          understandable profile, then helps your team decide who to contact
          first.
        </p>
        <p className="availability-note">
          Signal availability depends on connected data providers and market
          coverage.
        </p>
      </div>

      <div className="signal-cloud">
        <div className="signal-core">
          <span>
            Seller
            <br />
            intent
          </span>
          <b>Live</b>
        </div>

        {SELLER_SIGNALS.map((signal, index) => (
          <span className={`signal-chip chip-${index + 1}`} key={signal}>
            {signal}
          </span>
        ))}

        <div className="orbit orbit-one" />
        <div className="orbit orbit-two" />
      </div>
    </section>
  );
}
