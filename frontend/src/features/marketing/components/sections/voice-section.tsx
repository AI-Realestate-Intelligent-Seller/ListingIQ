/* AI Voice section — dark green bg, text LEFT, SVG RIGHT.
   Style inspired by lp-stats (deep green band) but two-column.
   Reuses lp-team layout classes; background + text colour overridden
   via the lp-voice modifier. */
import { VoiceCallIllustration } from "../../data/svg-components/VoiceCallIllustration";

export function VoiceSection() {
  return (
    <section className="lp-team lp-voice" id="voice">
      <div className="lp-team-inner">

        {/* LEFT — copy (text first in DOM = left column) */}
        <div className="lp-team-copy lp-voice-copy">
          <p className="lp-eyebrow lp-eyebrow-light">AI Voice Calling</p>

          <h2>
            Your leads, reached
            <br />
            by voice  automatically.
          </h2>

          <p>
            ListingIQ&apos;s AI can reach homeowners by phone, carry a natural
            conversation, and take notes so your agents step in already
            knowing what was said and what to do next.
          </p>

          <div className="lp-team-roles lp-voice-roles">
            <div className="lp-role-pill lp-voice-pill">
              <strong>AI-led outbound calls</strong>
              <span>
                Opens the conversation naturally, without scripts
                that sound scripted.
              </span>
            </div>
            <div className="lp-role-pill lp-voice-pill">
              <strong>Automatic call notes</strong>
              <span>
                Every call is summarised so agents know the context
                before they pick up the phone.
              </span>
            </div>
            <div className="lp-role-pill lp-voice-pill">
              <strong>Clean agent handoff</strong>
              <span>
                When the owner is ready, the call transfers to a real
                agent with full context attached.
              </span>
            </div>
          </div>
        </div>

        {/* RIGHT — SVG illustration */}
        <div className="lp-team-visual lp-voice-visual">
          <VoiceCallIllustration />
        </div>

      </div>
    </section>
  );
}
