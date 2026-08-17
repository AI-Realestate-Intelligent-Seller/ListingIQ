import { CAPABILITIES } from "../../data/landing-page-data";
import { SectionLabel } from "../section-label";

export function CapabilitiesSection() {
  return (
    <section className="intro section" id="solutions">
      <SectionLabel number="01">The difference</SectionLabel>

      <div className="intro-heading">
        <h2>
          Seller opportunities are scattered.
          <br />
          <em>Your workflow shouldn’t be.</em>
        </h2>
        <p>
          Most tools give you another list. ListingIQ gives every person in your
          brokerage the right next action—from first signal to qualified
          conversation.
        </p>
      </div>

      <div className="capability-grid">
        {CAPABILITIES.map((capability) => (
          <article className="capability-card" key={capability.number}>
            <div className="card-top">
              <span>{capability.number}</span>
              <b>↗</b>
            </div>

            <div className={`capability-art art-${capability.number}`}>
              <CapabilityArtwork number={capability.number} />
            </div>

            <span className="card-tag">{capability.tag}</span>
            <h3>{capability.title}</h3>
            <p>{capability.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

type CapabilityArtworkProps = {
  number: (typeof CAPABILITIES)[number]["number"];
};

function CapabilityArtwork({ number }: CapabilityArtworkProps) {
  if (number === "01") {
    return (
      <>
        <i className="ring ring-one" />
        <i className="ring ring-two" />
        <span className="pulse">94</span>
      </>
    );
  }

  if (number === "02") {
    return (
      <div className="conversation-art">
        <span>Voice</span>
        <i />
        <i />
        <i />
        <i />
        <i />
        <b>SMS</b>
      </div>
    );
  }

  return (
    <div className="handoff-art">
      <span>AI</span>
      <i>→</i>
      <b>JL</b>
    </div>
  );
}
