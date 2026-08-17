import Image from "next/image";

import { WORKFLOW_STEPS } from "../../data/landing-page-data";
import { SectionLabel } from "../section-label";

export function WorkflowSection() {
  return (
    <section className="workflow section" id="workflow">
      <div className="workflow-visual">
        <Image
          src="/images/chicago-neighborhood.jpg"
          alt="Chicago skyline beyond a residential neighborhood"
          fill
          sizes="(max-width: 900px) 100vw, 50vw"
        />
        <div className="photo-shade" />
        <div className="workflow-overlay">
          <span>Live market pulse</span>
          <strong>Chicago · 60647</strong>
          <div className="pulse-bars">
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
          <small>18 new seller signals today</small>
        </div>
      </div>

      <div className="workflow-content">
        <SectionLabel number="02" light>
          One continuous workflow
        </SectionLabel>
        <h2>From a quiet signal to a real conversation.</h2>
        <p className="workflow-lede">
          ListingIQ keeps data, outreach, qualification, and agent follow-up
          connected—so context never gets lost between tools.
        </p>

        <div className="step-list">
          {WORKFLOW_STEPS.map((step, index) => (
            <div className="step" key={step.title}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.description}</p>
              </div>
              <b>↗</b>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
