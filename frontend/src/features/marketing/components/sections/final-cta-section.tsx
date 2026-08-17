import Link from "next/link";

import { ArrowIcon } from "@/components/icons/arrow-icon";

export function FinalCtaSection() {
  return (
    <section className="final-cta section">
      <div className="cta-orbit" />
      <div className="cta-content">
        <span>THE NEXT CONVERSATION STARTS HERE</span>
        <h2>
          Turn seller signals
          <br />
          into real opportunities.
        </h2>
        <p>
          Give your brokerage a clearer way to discover, qualify, assign, and
          follow up.
        </p>
        <div>
          <Link className="button button-light button-large" href="/register">
            Create your account
            <ArrowIcon />
          </Link>
          <a href="mailto:hello@ListingIQ.ai">Talk to our team</a>
        </div>
      </div>
    </section>
  );
}
