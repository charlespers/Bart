/* Bart "script" — the simulated pipeline narration.
   Real ./run output is terminal-y; here we convert it into bart's chatty
   voice while preserving a few mono lines for texture. Each step has an
   estimated delay so the run feels live. */

/* Each step is a function (ctx) => ReactNode so we can interpolate live values
   (file count, day count, subject, etc.) without string-parsing tricks. */
const BART_PIPELINE = [
  { wait: 350,  who: "bart", text: () => <>hi! i'll start by peeking at the files you dropped in…</> },
  { wait: 900,  who: "sys",  text: ({ n }) => <>scanning materials/ … found <em>{n}</em> files</> },
  { wait: 600,  who: "bart", text: () => <>great. extracting the text now — pdfs, slides, docs, all of it.</> },
  { wait: 1100, who: "sys",  text: ({ chars }) => <>extracting corpus … ok <em>({chars.toLocaleString()} chars)</em></> },
  { wait: 500,  who: "bart", text: () => <>that's a healthy stack of notes. let me figure out how to budget the days.</> },
  { wait: 1300, who: "sys",  text: ({ days }) => <>planning <em>{days}</em>-day schedule …</> },
  { wait: 900,  who: "bart", text: () => <>okay — i've picked the topics that show up most in your past exams and pinned the trickier ones earlier. now sending out the researcher agents in parallel.</> },
  { wait: 1500, who: "sys",  text: ({ days, parallel }) => <>researcher × <em>{days}</em> → haiku  ·  parallel={parallel}</> },
  { wait: 800,  who: "bart", text: () => <>they'll fan through your corpus and pull the right snippets for each lesson. then i hand those briefs to the author.</> },
  { wait: 1700, who: "sys",  text: () => <>author → drafting day 1: <em>foundations &amp; notation</em></> },
  { wait: 1100, who: "bart", text: () => <>writing dense, with worked examples and quick-checks. i'll never put anything in here that isn't grounded in your notes — promise.</> },
  { wait: 1500, who: "sys",  text: () => <>critic → grading 84/100 ↑ accepted</> },
  { wait: 600,  who: "bart", text: () => <>the critic agent just gave it a pass. moving on.</> },
  { wait: 1400, who: "sys",  text: ({ days }) => <>author → {Array.from({ length: Math.min(days, 7) }, (_, i) => `day ${i + 2}`).slice(0, Math.max(0, Math.min(days, 7) - 1)).join(" · ") || "wrapping up"}</> },
  { wait: 1900, who: "bart", text: () => <>composing schematics, mnemonics, and your short study guide on the side. almost there.</> },
  { wait: 1300, who: "sys",  text: () => <>practice exam → 3-hour mock + answer key</> },
  { wait: 900,  who: "bart", text: () => <>and the practice exam. this one is serious — a real 3-hour run with a key you can self-grade.</> },
  { wait: 1300, who: "sys",  text: () => <>rendering html packet · katex · search index</> },
  { wait: 1000, who: "bart-final", text: () => <>all done! your packet is ready below. open day 1 first — it has the orientation and the notation table you'll want pinned.</> },
];

/* friendly variants for messages so re-runs feel a little different */
const VARIANT_INTRO = [
  "hi! i'll start by peeking at the files you dropped in…",
  "okay — i can see the materials. let me have a look.",
  "ooh, fresh notes. give me a sec to flip through them.",
];

/* Templates for generated lesson list, lightly customised by subject + days */
function buildPacket(subject, days) {
  const day_titles = [
    "foundations & notation",
    "core mechanism walkthroughs",
    "reactions you keep mis-classifying",
    "stereochemistry refresher",
    "selectivity & directing effects",
    "synthesis problems, slow then fast",
    "rapid review — the morning of",
  ];
  const lessons = [];
  for (let i = 0; i < Math.min(days, 30); i++) {
    const t = day_titles[i] || `mixed practice — block ${i - 6}`;
    lessons.push({
      id: `day_${String(i + 1).padStart(2, "0")}`,
      n: i + 1,
      title: t,
      length: 900 + Math.floor(Math.random() * 700),
      cards: 8 + Math.floor(Math.random() * 5),
    });
  }
  const top = [
    { glyph: "MD", label: "Master plan",        sub: "00 · what to study, on what day, why" },
    { glyph: "SC", label: "Schematics",         sub: "01 · diagrams · formulae · traps" },
    { glyph: "WN", label: "Whimsical notes",    sub: "02 · mnemonics that stick" },
    { glyph: "SS", label: "Short study guide",  sub: "03 · the 60-minute version" },
    { glyph: "PE", label: "Practice exam",      sub: "04 · 3-hour mock + key" },
  ];
  return { subject, days, lessons, top };
}

window.BART_PIPELINE = BART_PIPELINE;
window.VARIANT_INTRO = VARIANT_INTRO;
window.buildPacket = buildPacket;
