import Image from "next/image";

import { SectionLabel } from "../section-label";

export function SecuritySection() {
  return (
    <section className="security section" id="security">
      <div className="security-image">
        <Image
          src="/images/home-exterior.jpg"
          alt="Welcoming residential home exterior at dusk"
          fill
          sizes="(max-width: 900px) 100vw, 45vw"
        />
        <div className="security-badge">
          <span>✓</span>
          <div>
            <strong>Built for responsible outreach</strong>
            <small>Opt-outs and activity records stay visible.</small>
          </div>
        </div>
      </div>

      <div className="security-copy">
        <SectionLabel number="06">Trust by design</SectionLabel>
        <h2>
          Clear access.
          <br />
          <em>Responsible action.</em>
        </h2>
        <p>
          Protect brokerage data and support responsible seller engagement
          without burying your team in technical controls.
        </p>

        <div className="security-list">
          <div>
            <span>01</span>
            <strong>Role-based access</strong>
            <p>Each person sees only what they need.</p>
          </div>
          <div>
            <span>02</span>
            <strong>Brokerage separation</strong>
            <p>Organization data stays clearly partitioned.</p>
          </div>
          <div>
            <span>03</span>
            <strong>Outreach controls</strong>
            <p>Support for opt-outs, DNC workflows, and activity records.</p>
          </div>
        </div>

        <small className="legal-note">
          Brokerages are responsible for operating outreach according to
          applicable laws, consent requirements, and provider rules.
        </small>
      </div>
    </section>
  );
}
