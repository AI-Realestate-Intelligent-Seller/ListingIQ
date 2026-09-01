/* ─── Voice Call Illustration ───────────────────────────────────────────────
   Used in: voice-section
   Shows an AI voice call log panel in the ListingIQ product style.
   Same visual language as DashboardIllustration and LeadIntelligenceCard:
   flat product-feel, green brand, same font sizes/colors/borders.
────────────────────────────────────────────────────────────────────────────── */
export function VoiceCallIllustration() {
  const G      = "#1f5c4d";
  const GSOFT  = "#e4eeea";
  const GMID   = "#4a8c7a";
  const STROKE = "#c8d4d0";   /* slightly darker than default so card edge shows on white */
  const TEXT   = "#111111";
  const MUTED  = "#8a9a95";
  const WHITE  = "#ffffff";

  /* Waveform bar heights — simulating a voice amplitude trace */
  const bars = [14, 22, 32, 18, 40, 28, 44, 36, 24, 46, 38, 26, 44, 32, 20, 42, 30, 16, 38, 28];

  return (
    <svg
      viewBox="0 0 440 380"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="ListingIQ AI voice call log illustration"
      role="img"
      style={{ width: "100%", height: "100%", display: "block" }}
    >
      {/* ── Outer card ── */}
      <rect x="8" y="8" width="424" height="364" rx="10" fill={WHITE} stroke={STROKE} strokeWidth="1" />

      {/* ── Header bar ── */}
      <rect x="8" y="8" width="424" height="48" rx="10" fill={G} />
      <rect x="8" y="36" width="424" height="20" fill={G} />

      {/* Header label */}
      <text
        x="36" y="36"
        fontSize="11" fontWeight="700"
        fill={WHITE} fontFamily="system-ui,sans-serif"
        letterSpacing="0.06em"
      >
        AI VOICE CALLS
      </text>

      {/* Live indicator dot */}
      <circle cx="390" cy="32" r="5" fill="#7fd0bd" />
      <text
        x="380" y="32"
        textAnchor="end" dominantBaseline="middle"
        fontSize="9" fontWeight="600"
        fill="rgba(255,255,255,.7)" fontFamily="system-ui,sans-serif"
      >
        Live
      </text>

      {/* ── Active call row ── */}
      <rect x="24" y="70" width="392" height="72" rx="6" fill={GSOFT} />

      {/* Pulse ring around avatar — indicates live call */}
      <circle cx="50" cy="106" r="18" stroke={G} strokeWidth="1.5" strokeOpacity="0.25" />
      <circle cx="50" cy="106" r="13" fill={G} />
      <text
        x="50" y="106"
        textAnchor="middle" dominantBaseline="middle"
        fontSize="8" fontWeight="800"
        fill={WHITE} fontFamily="system-ui,sans-serif"
      >
        AI
      </text>

      {/* Call meta */}
      <text x="76" y="96"  fontSize="11" fontWeight="700" fill={TEXT}  fontFamily="system-ui,sans-serif">2147 N Oakley Ave,Elena Park</text>
      <text x="76" y="110" fontSize="9"  fill={MUTED} fontFamily="system-ui,sans-serif">Outbound · Chicago, IL · High equity signal</text>

      {/* Duration + status badges */}
      <rect x="76"  y="118" width="46" height="16" rx="3" fill="#dcfce7" />
      <text x="99"  y="126" textAnchor="middle" dominantBaseline="middle" fontSize="8" fontWeight="600" fill="#166534" fontFamily="system-ui,sans-serif">● Active</text>
      <rect x="130" y="118" width="42" height="16" rx="3" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="151" y="126" textAnchor="middle" dominantBaseline="middle" fontSize="8" fill={MUTED} fontFamily="system-ui,sans-serif">1m 42s</text>

      {/* ── Waveform panel ── */}
      <rect x="24" y="152" width="392" height="64" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />
      <text x="36" y="167" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">VOICE ACTIVITY</text>

      {/* Waveform bars — centered vertically in the panel */}
      {bars.map((h, i) => {
        const x = 36 + i * 18;
        const y = 186 - h / 2;
        const isRecent = i >= 14;
        return (
          <rect
            key={i}
            x={x}
            y={y}
            width="10"
            height={h}
            rx="2"
            fill={isRecent ? G : GMID}
            opacity={isRecent ? 1 : 0.45}
          />
        );
      })}

      {/* ── AI notes panel ── */}
      <rect x="24" y="228" width="392" height="100" rx="6" fill={WHITE} stroke={STROKE} strokeWidth="1" />

      {/* Notes header */}
      <text x="36" y="244" fontSize="9" fontWeight="700" fill={MUTED} fontFamily="system-ui,sans-serif" letterSpacing="0.08em">AI CALL NOTES · In progress</text>
      <line x1="24" y1="252" x2="416" y2="252" stroke={STROKE} strokeWidth="1" />

      {/* Note lines */}
      <circle cx="44" cy="266" r="3" fill={G} />
      <text x="56" y="266" dominantBaseline="middle" fontSize="10" fill={TEXT} fontFamily="system-ui,sans-serif">Owner acknowledged the property inquiry</text>

      <circle cx="44" cy="284" r="3" fill={GMID} />
      <text x="56" y="284" dominantBaseline="middle" fontSize="10" fill={TEXT} fontFamily="system-ui,sans-serif">Mentioned considering options in the next few months</text>

      <circle cx="44" cy="302" r="3" fill={STROKE} />
      <text x="56" y="302" dominantBaseline="middle" fontSize="10" fill={MUTED} fontFamily="system-ui,sans-serif">Appointment discussion  pending confirmation</text>

      {/* ── Handoff badge ── */}
      <rect x="24" y="340" width="392" height="22" rx="5" fill={GSOFT} />
      <circle cx="40" cy="351" r="4" fill={G} />
      <text x="52" y="351" dominantBaseline="middle" fontSize="9" fontWeight="600" fill={G} fontFamily="system-ui,sans-serif">Ready for agent handoff · Jordan Lee</text>
      <text x="408" y="351" textAnchor="end" dominantBaseline="middle" fontSize="8" fill={MUTED} fontFamily="system-ui,sans-serif">Score 94</text>
    </svg>
  );
}
