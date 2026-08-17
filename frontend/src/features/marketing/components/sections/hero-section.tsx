import Link from "next/link";

import { ArrowIcon } from "@/components/icons/arrow-icon";

import { ProductPreview } from "../product-preview";

export function HeroSection() {
  return (
    <section className="hero" id="product">
      <div className="hero-orb hero-orb-one" />
      <div className="hero-orb hero-orb-two" />

      <div className="hero-copy">
        <div className="eyebrow">
          <span /> AI seller intelligence for modern brokerages
        </div>
        <h1>
          Know who may sell.
          <br />
          <em>Know what to do next.</em>
        </h1>
        <p>
          ListingIQ turns fragmented seller signals into prioritized
          opportunities, natural conversations, and clear agent action—all in
          one simple platform.
        </p>

        <div className="hero-actions">
          <Link className="button button-large" href="/register">
            Create your account
            <ArrowIcon />
          </Link>
          <a className="text-link" href="#workflow">
            <span className="play-icon">▶</span> See how it works
          </a>
        </div>

        <div className="hero-proof">
          <div className="proof-avatars">
            <span>BF</span>
            <span>MK</span>
            <span>+700</span>
          </div>
          <p>
            Built around the real workflows of
            <br />
            brokerages, teams, and agents.
          </p>
        </div>
      </div>

      <ProductPreview />
    </section>
  );
}
