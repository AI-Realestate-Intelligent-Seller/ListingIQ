import Image from "next/image";

import { CheckIcon } from "@/components/icons/check-icon";

import { SectionLabel } from "../section-label";

export function HumanHandoffSection() {
  return (
    <section className="human-section section">
      <div className="human-copy">
        <SectionLabel number="04" light>
          Intelligence, then human judgment
        </SectionLabel>
        <h2>
          AI opens the door.
          <br />
          <em>Your agents build trust.</em>
        </h2>
        <p>
          Every qualified handoff includes what the homeowner said, why they
          may be motivated, and the recommended next action. Agents arrive
          prepared—not cold.
        </p>

        <div className="human-points">
          <span>
            <CheckIcon /> Shared conversation history
          </span>
          <span>
            <CheckIcon /> Clear motivation and timeline
          </span>
          <span>
            <CheckIcon /> Smart agent assignment
          </span>
        </div>
      </div>

      <div className="human-photo">
        <Image
          src="/images/agent-consultation.jpg"
          alt="Real-estate professional consulting with homeowners in a bright home"
          fill
          sizes="(max-width: 900px) 100vw, 50vw"
        />
        <div className="photo-note">
          <span>Qualified handoff</span>
          <strong>“Considering a move this fall.”</strong>
          <small>Timeline · 3–6 months</small>
        </div>
      </div>
    </section>
  );
}
