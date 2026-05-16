/* Bart loaf SVG, exposed as React component.
   Two sizes — the big mascot and the tiny chat-avatar. Same geometry,
   matches assets/bart-loaf.svg from the codebase. */

function BartLoaf({ size = 168, blink = true, className = "bart-loaf" }) {
  return (
    <svg
      className={className}
      viewBox="0 0 220 200"
      width={size}
      height={size * 200 / 220}
      role="img"
      aria-label="bart"
    >
      <defs>
        <radialGradient id="loaf-body" cx="36%" cy="28%" r="80%">
          <stop offset="0%"   stopColor="#fbf9f4"/>
          <stop offset="50%"  stopColor="#f4f1ea"/>
          <stop offset="100%" stopColor="#d9d3c4"/>
        </radialGradient>
        <radialGradient id="loaf-accent" cx="36%" cy="30%" r="80%">
          <stop offset="0%"   stopColor="#e88a6a"/>
          <stop offset="55%"  stopColor="#c96442"/>
          <stop offset="100%" stopColor="#9a4628"/>
        </radialGradient>
        <radialGradient id="loaf-floor" cx="50%" cy="50%" r="50%">
          <stop offset="0%"   stopColor="rgba(34,30,25,0.30)"/>
          <stop offset="60%"  stopColor="rgba(34,30,25,0.08)"/>
          <stop offset="100%" stopColor="rgba(34,30,25,0)"/>
        </radialGradient>
      </defs>

      <ellipse cx="110" cy="178" rx="80" ry="9" fill="url(#loaf-floor)"/>

      <rect x="70"  y="158" width="22" height="16" rx="8" fill="url(#loaf-accent)"/>
      <rect x="128" y="158" width="22" height="16" rx="8" fill="url(#loaf-accent)"/>
      <ellipse cx="81"  cy="161" rx="6" ry="2" fill="#fff" opacity="0.45"/>
      <ellipse cx="139" cy="161" rx="6" ry="2" fill="#fff" opacity="0.45"/>

      <rect x="22" y="60" width="176" height="106" rx="53" fill="url(#loaf-body)"/>

      <ellipse cx="110" cy="158" rx="84" ry="9" fill="rgba(34,30,25,0.10)"/>
      <ellipse cx="78"  cy="84"  rx="48" ry="14" fill="#fff" opacity="0.45"/>

      <ellipse cx="110" cy="56" rx="11" ry="9" fill="url(#loaf-accent)"/>
      <ellipse cx="106" cy="53" rx="3.4" ry="2.4" fill="#fff" opacity="0.65"/>

      <g className={blink ? "bart-blink" : ""} style={{ transformOrigin: "110px 106px" }}>
        <ellipse cx="86"  cy="106" rx="5" ry="8.5" fill="#221f1b"/>
        <ellipse cx="134" cy="106" rx="5" ry="8.5" fill="#221f1b"/>
        <ellipse cx="84"  cy="103" rx="1.4" ry="1.9" fill="#fff" opacity="0.95"/>
        <ellipse cx="132" cy="103" rx="1.4" ry="1.9" fill="#fff" opacity="0.95"/>
        <ellipse cx="86"  cy="111" rx="3.6" ry="0.8" fill="#3a342d" opacity="0.4"/>
        <ellipse cx="134" cy="111" rx="3.6" ry="0.8" fill="#3a342d" opacity="0.4"/>
      </g>

      <path d="M 100 128 Q 110 136 120 128" fill="none" stroke="#221f1b"
            strokeWidth="2.2" strokeLinecap="round"/>

      <ellipse cx="68"  cy="124" rx="6" ry="2.6" fill="#c96442" opacity="0.28"/>
      <ellipse cx="152" cy="124" rx="6" ry="2.6" fill="#c96442" opacity="0.28"/>
    </svg>
  );
}

function MiniLoaf() {
  /* Tiny chat-avatar variant — same character, simpler. */
  return (
    <svg viewBox="0 0 220 200" width="36" height="32" aria-hidden="true">
      <defs>
        <radialGradient id="mini-body" cx="36%" cy="28%" r="80%">
          <stop offset="0%"   stopColor="#fbf9f4"/>
          <stop offset="50%"  stopColor="#f4f1ea"/>
          <stop offset="100%" stopColor="#d9d3c4"/>
        </radialGradient>
        <radialGradient id="mini-accent" cx="36%" cy="30%" r="80%">
          <stop offset="0%"   stopColor="#e88a6a"/>
          <stop offset="55%"  stopColor="#c96442"/>
          <stop offset="100%" stopColor="#9a4628"/>
        </radialGradient>
      </defs>
      <rect x="22" y="60" width="176" height="106" rx="53" fill="url(#mini-body)"/>
      <ellipse cx="78" cy="84" rx="48" ry="14" fill="#fff" opacity="0.45"/>
      <ellipse cx="110" cy="56" rx="11" ry="9" fill="url(#mini-accent)"/>
      <ellipse cx="86"  cy="106" rx="5" ry="8.5" fill="#221f1b"/>
      <ellipse cx="134" cy="106" rx="5" ry="8.5" fill="#221f1b"/>
      <ellipse cx="84"  cy="103" rx="1.4" ry="1.9" fill="#fff" opacity="0.95"/>
      <ellipse cx="132" cy="103" rx="1.4" ry="1.9" fill="#fff" opacity="0.95"/>
      <path d="M 100 128 Q 110 136 120 128" fill="none" stroke="#221f1b"
            strokeWidth="2.4" strokeLinecap="round"/>
      <ellipse cx="68"  cy="124" rx="6" ry="2.6" fill="#c96442" opacity="0.28"/>
      <ellipse cx="152" cy="124" rx="6" ry="2.6" fill="#c96442" opacity="0.28"/>
    </svg>
  );
}

function Wordmark({ onClick }) {
  return (
    <span
      className="mark"
      onClick={onClick}
      style={onClick ? { cursor: "pointer" } : undefined}
    >
      <span>bart</span><span className="dot">.</span>
    </span>
  );
}

window.BartLoaf = BartLoaf;
window.MiniLoaf = MiniLoaf;
window.Wordmark = Wordmark;
