/* bart — main app */

const { useState, useEffect, useRef, useMemo, useCallback } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent":      "#c96442",
  "showRunDots": true,
  "voice":       "warm"
}/*EDITMODE-END*/;

/* ─── small DOM helpers ─── */
function useOnce(fn) { const r = useRef(false); useEffect(() => { if (r.current) return; r.current = true; fn(); }, []); }

/* Bart's pipeline stages (in order). Each entry maps a substring from the
   server's bart-voice line to a progress floor + an expected duration that
   drives the intra-stage tween. The progress bar steps to `pct` when bart
   enters a stage, then creeps toward the next stage's floor over `tweenSec`,
   so it never freezes if a stage takes longer than expected. */
const STAGES = [
  { match: "peeking at your notes",          pct:  5, tweenSec:  20 },
  { match: "stitching everything together",  pct: 10, tweenSec:  20 },
  { match: "sanity-call to the model",       pct: 15, tweenSec:  30 },
  { match: "budget the days",                pct: 22, tweenSec:  45 },
  { match: "researcher agents",              pct: 35, tweenSec:  90 },
  { match: "writing dense",                  pct: 55, tweenSec: 240 },  // many days
  { match: "critic agent",                   pct: 78, tweenSec:  60 },
  { match: "composing schematics",           pct: 84, tweenSec:  60 },
  { match: "writing the mnemonics",          pct: 88, tweenSec:  45 },
  { match: "60-minute version",              pct: 92, tweenSec:  45 },
  { match: "now the practice exam",          pct: 95, tweenSec: 120 },
  { match: "rendering the html",             pct: 98, tweenSec:  30 },
  { match: "all done",                       pct:100, tweenSec:   1 },
];
function findStageIdx(line) {
  if (!line) return -1;
  const lo = line.toLowerCase();
  for (let i = STAGES.length - 1; i >= 0; i--) {
    if (lo.includes(STAGES[i].match)) return i;
  }
  return -1;
}

/* ─────────── WordSelect (inline plain-language dropdown) ─────────── */
function WordSelect({ value, onChange, options }) {
  const [open, setOpen] = useState(false);
  const wrap = useRef(null);
  useEffect(() => {
    function onDoc(e) { if (wrap.current && !wrap.current.contains(e.target)) setOpen(false); }
    if (open) document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const sel = options.find(o => o.value === value) || options[0];
  return (
    <span ref={wrap} className={"word-select" + (open ? " is-open" : "")}>
      <span className="word" onClick={() => setOpen(o => !o)}>{sel.label}</span>
      <span className="menu" role="listbox">
        {options.map(o => (
          <span
            key={o.value}
            className={"opt" + (o.value === value ? " is-active" : "")}
            onClick={() => { onChange(o.value); setOpen(false); }}
          >
            <span>{o.label}</span>
            {o.desc && <span className="desc">{o.desc}</span>}
          </span>
        ))}
      </span>
    </span>
  );
}

/* ─────────── WordInput (inline freeform word) ─────────── */
function WordInput({ value, onChange, placeholder }) {
  const sizerRef = useRef(null);
  const [w, setW] = useState(60);
  const display = (value && value.length) ? value : (placeholder || "");
  useEffect(() => {
    if (sizerRef.current) {
      // measure rendered text and add a small cursor buffer
      setW(Math.ceil(sizerRef.current.offsetWidth) + 6);
    }
  }, [display]);
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <span
        ref={sizerRef}
        aria-hidden="true"
        style={{
          position: "absolute", visibility: "hidden", whiteSpace: "pre",
          font: "inherit", letterSpacing: "inherit", fontWeight: 600,
          pointerEvents: "none",
        }}
      >{display || " "}</span>
      <input
        className="word-input"
        style={{ width: `${w}px` }}
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </span>
  );
}

/* ─────────── File chip glyph by ext ─────────── */
function extOf(name) {
  const m = /\.([a-z0-9]+)$/i.exec(name); return (m ? m[1] : "").toLowerCase();
}
function glyphFor(ext) {
  return { pdf: "PDF", docx: "DOC", doc: "DOC", pptx: "PPT", ppt: "PPT", md: "MD", txt: "TXT" }[ext] || "FILE";
}

/* ─────────── Drop zone overlay ─────────── */
function useGlobalDrop(onFiles) {
  const [over, setOver] = useState(false);
  useEffect(() => {
    let counter = 0;
    function dragEnter(e) {
      if (!e.dataTransfer?.types?.includes("Files")) return;
      counter += 1; setOver(true); e.preventDefault();
    }
    function dragLeave(e) { counter -= 1; if (counter <= 0) { counter = 0; setOver(false); } }
    function dragOver(e) { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); }
    function drop(e) {
      e.preventDefault(); counter = 0; setOver(false);
      const fs = Array.from(e.dataTransfer?.files || []);
      if (fs.length) onFiles(fs);
    }
    window.addEventListener("dragenter", dragEnter);
    window.addEventListener("dragleave", dragLeave);
    window.addEventListener("dragover", dragOver);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", dragEnter);
      window.removeEventListener("dragleave", dragLeave);
      window.removeEventListener("dragover", dragOver);
      window.removeEventListener("drop", drop);
    };
  }, [onFiles]);
  return over;
}

/* ─────────── Chat message ─────────── */
function Msg({ who, children, system, typing }) {
  return (
    <div className={"msg " + (system ? "system" : "")}>
      <div className="mini-loaf">{!system && <MiniLoaf />}</div>
      <div className="body">
        <div className="who">{system ? "" : "bart"}</div>
        <div className="text">
          {children}
          {typing && <span className="typing"><span></span><span></span><span></span></span>}
        </div>
      </div>
    </div>
  );
}

/* ─────────── Lesson list (packet) ─────────── */
function Packet({ pkt, subject, onOpen, cost }) {
  return (
    <div className="packet">
      <div className="heading">
        your packet — {pkt.lessons.length} day plan · {subject || "your course"}
        <span className="pkt-cost">{cost ? `≈ ${cost} · ` : ""}{pkt.lessons.length + pkt.top.length} files</span>
      </div>

      <div className="top-files">
        {pkt.top.map(t => (
          <div key={t.glyph} className="top-file" onClick={() => onOpen({ kind: "top", id: t.glyph })}>
            <span className="glyph">{t.glyph}</span>
            <span className="l">{t.label}<span className="sub">{t.sub}</span></span>
          </div>
        ))}
      </div>

      <div className="lesson-list">
        {pkt.lessons.map(l => (
          <div key={l.id} className="lesson-row" onClick={() => onOpen({ kind: "day", id: l.id })}>
            <span className="num">day {String(l.n).padStart(2, "0")}</span>
            <span className="title">
              {l.title}
            </span>
            <span className="stat">{l.length} lines · {l.cards} cards</span>
            <span className="open">open ↗</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ─────────── Pettable bart — hover squish + click sparkle + little reactions ─────────── */
const PET_REACTIONS = [
  "hi :)",
  "*purrs in loaf*",
  "ok ok i'll get to studying",
  "you're a good one",
  "stop it (don't)",
  "i'm baking, you're petting",
  "okay one more",
  "ily",
];
function PettableBart() {
  const wrapRef = useRef(null);
  const [pets, setPets] = useState(0);
  const [sparks, setSparks] = useState([]);    // [{ id, dx }]
  const [reaction, setReaction] = useState(null);
  function pet(e) {
    const id = Math.random().toString(36).slice(2);
    const dx = Math.round((Math.random() - 0.5) * 36);  // -18..18px wobble
    setSparks(prev => [...prev, { id, dx }]);
    setTimeout(() => setSparks(prev => prev.filter(s => s.id !== id)), 950);
    setPets(prev => prev + 1);
    // briefly add a .petted class to retrigger the squish animation
    const w = wrapRef.current;
    if (w) {
      w.classList.remove("petted");
      // force reflow so the class re-applies cleanly
      void w.offsetWidth;
      w.classList.add("petted");
    }
    // every 3rd pet, surface a tiny reaction line under bart
    if ((pets + 1) % 3 === 0) {
      const line = PET_REACTIONS[Math.floor(Math.random() * PET_REACTIONS.length)];
      setReaction(line);
      setTimeout(() => setReaction(curr => curr === line ? null : curr), 1800);
    }
  }
  return (
    <div className="bart-wrap" ref={wrapRef} onClick={pet}
         role="button" aria-label="pet bart"
         title="pet bart">
      <BartLoaf size={168} />
      {sparks.map(s => (
        <span key={s.id} className="bart-spark" style={{ "--dx": s.dx + "px" }}>♡</span>
      ))}
      <span className="bart-meter">
        {reaction || (pets === 0 ? "(click me)" : pets === 1 ? "1 pet" : `${pets} pets`)}
      </span>
    </div>
  );
}

/* ─────────── Settings: connect your Claude account ─────────── */
function Settings({ onBack }) {
  const [status, setStatus] = useState(null);   // { connected, login_in_progress }
  const [phase, setPhase] = useState("idle");   // idle | starting | waiting | submitting | done | error
  const [deviceUrl, setDeviceUrl] = useState(null);
  const [codeInput, setCodeInput] = useState("");
  const [errMsg, setErrMsg] = useState(null);
  const esRef = useRef(null);

  async function loadStatus() {
    try {
      const r = await fetch("/api/auth/anthropic", { credentials: "same-origin" });
      if (r.ok) setStatus(await r.json());
    } catch (_) { /* ignore */ }
  }
  useEffect(() => { loadStatus(); }, []);
  useEffect(() => () => { if (esRef.current) esRef.current.close(); }, []);

  async function submitCode() {
    const code = codeInput.trim();
    if (!code) return;
    setPhase("submitting"); setErrMsg(null);
    try {
      const r = await fetch("/api/auth/anthropic/code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ code }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("waiting");  // stay in waiting; user can retry
        setErrMsg(j.detail || `couldn't submit (${r.status})`);
        return;
      }
      setCodeInput("");
      // stay in "waiting" — the SSE stream will fire `ok` or `failed` once the
      // subprocess finishes writing credentials.
      setPhase("waiting");
    } catch (e) {
      setPhase("waiting");
      setErrMsg(`network error: ${e.message}`);
    }
  }

  async function connect() {
    setPhase("starting"); setDeviceUrl(null); setCodeInput(""); setErrMsg(null);
    try {
      const r = await fetch("/api/auth/anthropic/connect", {
        method: "POST", credentials: "same-origin",
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("error"); setErrMsg(j.detail || `couldn't start (${r.status})`);
        return;
      }
    } catch (e) {
      setPhase("error"); setErrMsg(`network error: ${e.message}`); return;
    }
    setPhase("waiting");
    const es = new EventSource("/api/auth/anthropic/events");
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.kind === "url") setDeviceUrl(data.url);
        if (data.kind === "ok") { setPhase("done"); loadStatus(); }
        if (data.kind === "failed") {
          setPhase("error");
          setErrMsg(data.error || `login failed (exit ${data.code ?? "?"})`);
        }
      } catch (_) {}
    };
    es.addEventListener("done", () => { es.close(); esRef.current = null; loadStatus(); });
    es.onerror = () => { es.close(); esRef.current = null; };
  }

  async function disconnect() {
    if (!confirm("disconnect your claude account from bart? your past packets stay; you'll need to reconnect before a new run.")) return;
    try {
      await fetch("/api/auth/anthropic", { method: "DELETE", credentials: "same-origin" });
    } catch (_) {}
    setPhase("idle"); setDeviceUrl(null); loadStatus();
  }

  const connected = status?.connected;

  return (
    <div className="packet" style={{ marginTop: 24 }}>
      <div className="heading">
        settings
        <span className="pkt-cost" onClick={onBack} style={{ cursor: "pointer" }}>
          ← back to bart
        </span>
      </div>

      <div style={{
        marginTop: 4, padding: "20px 22px",
        background: "var(--cream-hi)",
        borderRadius: 14,
        boxShadow: "inset 0 0 0 1px var(--cream-edge)",
      }}>
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: 10.5,
          color: "var(--ink-faint)", letterSpacing: "0.16em",
          textTransform: "uppercase", marginBottom: 8,
        }}>
          your claude account
        </div>

        <div style={{
          display: "flex", alignItems: "center", gap: 12,
          fontSize: 17, color: "var(--ink)", marginBottom: 6,
        }}>
          <span style={{
            display: "inline-block", width: 9, height: 9, borderRadius: 999,
            background: connected ? "var(--ok)" : "var(--ink-faint)",
          }} />
          {connected ? "connected" : "not connected"}
        </div>

        <div style={{
          color: "var(--ink-mute)", fontSize: 14, lineHeight: 1.55, marginBottom: 18,
        }}>
          bart needs your claude.ai login to actually run. {connected
            ? "you're set — start studying."
            : "click connect, sign in on claude.ai in the popup, come back here."}
        </div>

        {!connected && phase === "idle" && (
          <button className="run-btn" onClick={connect} style={{ padding: "12px 22px" }}>
            connect claude account
          </button>
        )}

        {phase === "starting" && (
          <div className="muted mono" style={{ fontSize: 12 }}>
            launching claude /login…
          </div>
        )}

        {phase === "waiting" && (
          <div>
            <div style={{ fontSize: 14, color: "var(--ink)", marginBottom: 10 }}>
              {deviceUrl
                ? <>open this in your browser, sign in, and authorize:</>
                : <>waiting for the login URL from the CLI…</>}
            </div>
            {deviceUrl && (
              <>
                <a href={deviceUrl} target="_blank" rel="noopener noreferrer"
                   style={{
                     display: "inline-block", padding: "12px 18px",
                     background: "var(--accent)", color: "var(--cream-hi)",
                     borderRadius: 999, fontFamily: "var(--font-mono)", fontSize: 12.5,
                     textDecoration: "none", letterSpacing: "0.04em",
                     wordBreak: "break-all", maxWidth: "100%",
                   }}>
                  open claude.com to sign in ↗
                </a>
                <ol style={{
                  marginTop: 18, paddingLeft: 18,
                  fontSize: 14, color: "var(--ink)", lineHeight: 1.55,
                }}>
                  <li>open that link, sign in to your claude.ai account, approve bart.</li>
                  <li>claude.com will redirect you to a page with a <strong>code</strong> in the URL or on the page.</li>
                  <li>paste the code below and click submit.</li>
                </ol>
                <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
                  <input
                    type="text" placeholder="paste your code here"
                    value={codeInput}
                    onChange={e => setCodeInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") submitCode(); }}
                    style={{
                      flex: 1, padding: "10px 14px",
                      background: "var(--cream)", border: "0", borderRadius: 10,
                      fontFamily: "var(--font-mono)", fontSize: 13,
                      boxShadow: "inset 0 0 0 1.5px var(--cream-edge)",
                    }}
                  />
                  <button onClick={submitCode}
                          className="run-btn"
                          style={{ padding: "10px 18px", fontSize: 14 }}
                          disabled={phase === "submitting" || !codeInput.trim()}>
                    {phase === "submitting" ? "submitting…" : "submit"}
                  </button>
                </div>
                {errMsg && (
                  <div style={{ color: "var(--accent-lo)", fontSize: 12, marginTop: 8 }}>
                    {errMsg}
                  </div>
                )}
              </>
            )}
            {!deviceUrl && (
              <div className="muted mono" style={{ fontSize: 11, marginTop: 14 }}>
                this page will update once the CLI prints the login URL.
              </div>
            )}
          </div>
        )}

        {phase === "done" && (
          <div style={{ color: "var(--ok)", fontSize: 14, fontWeight: 600 }}>
            ✓ connected — you can run bart now.
          </div>
        )}

        {phase === "error" && (
          <div>
            <div style={{ color: "var(--accent-lo)", fontSize: 13, marginBottom: 10 }}>
              {errMsg || "something went wrong."}
            </div>
            <button className="run-btn" onClick={connect} style={{ padding: "10px 18px", fontSize: 14 }}>
              try again
            </button>
          </div>
        )}

        {connected && phase !== "waiting" && (
          <div style={{ marginTop: 12 }}>
            <a href="#" onClick={e => { e.preventDefault(); disconnect(); }}
               style={{
                 fontFamily: "var(--font-mono)", fontSize: 11,
                 color: "var(--ink-faint)", textDecoration: "none",
                 letterSpacing: "0.08em",
               }}>
              disconnect ↗
            </a>
          </div>
        )}
      </div>

      <div className="muted mono" style={{
        fontSize: 11, color: "var(--ink-faint)",
        marginTop: 22, lineHeight: 1.6,
      }}>
        bart uses the `claude` CLI on the server with your credentials in an isolated workspace.
        we don't see your password — just the session token claude.ai gives us.
      </div>

      <SubscriptionCard />
      <ChangePasswordCard />
    </div>
  );
}

/* ─────────── Settings: subscription ─────────── */
function SubscriptionCard() {
  const [billing, setBilling] = useState(null);
  const [phase, setPhase] = useState("idle");
  const [errMsg, setErrMsg] = useState(null);

  function load() {
    fetch("/api/billing/status", { credentials: "same-origin" })
      .then(r => r.ok ? r.json() : null)
      .then(setBilling)
      .catch(() => {});
  }
  useEffect(() => { load(); }, []);

  // Surface a success toast when returning from Stripe checkout.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("subscribed") === "1") {
      setPhase("just-subscribed");
      url.searchParams.delete("subscribed");
      window.history.replaceState({}, "", url.pathname + (url.search || ""));
      // give the webhook a beat to land, then refresh status
      setTimeout(load, 1500);
    }
  }, []);

  async function subscribe() {
    setPhase("starting"); setErrMsg(null);
    try {
      const r = await fetch("/api/billing/checkout", { method: "POST", credentials: "same-origin" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("error");
        setErrMsg(j.detail || `couldn't start checkout (${r.status})`);
        return;
      }
      const { url } = await r.json();
      window.location.href = url;
    } catch (e) {
      setPhase("error");
      setErrMsg(`network error: ${e.message}`);
    }
  }

  async function manage() {
    setPhase("opening-portal"); setErrMsg(null);
    try {
      const r = await fetch("/api/billing/portal", { method: "POST", credentials: "same-origin" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("error");
        setErrMsg(j.detail || `couldn't open portal (${r.status})`);
        return;
      }
      const { url } = await r.json();
      window.location.href = url;
    } catch (e) {
      setPhase("error");
      setErrMsg(`network error: ${e.message}`);
    }
  }

  if (billing === null) return null;
  const gf     = billing.is_grandfathered;
  const status = billing.status || "free";
  const active = status === "active" || status === "trialing";

  return (
    <div style={{
      marginTop: 18, padding: "20px 22px",
      background: "var(--cream-hi)",
      borderRadius: 14,
      boxShadow: "inset 0 0 0 1px var(--cream-edge)",
    }}>
      <div style={{
        fontFamily: "var(--font-mono)", fontSize: 10.5,
        color: "var(--ink-faint)", letterSpacing: "0.16em",
        textTransform: "uppercase", marginBottom: 8,
      }}>
        plan
      </div>

      <div style={{
        display: "flex", alignItems: "center", gap: 12,
        fontSize: 17, color: "var(--ink)", marginBottom: 6,
      }}>
        <span style={{
          display: "inline-block", width: 9, height: 9, borderRadius: 999,
          background: (gf || active) ? "var(--ok)" : "var(--ink-faint)",
        }} />
        {gf
          ? "grandfathered — free forever, thanks for being early."
          : active
            ? "subscribed — $10/month"
            : status === "past_due" ? "past due — update payment"
            : status === "canceled" ? "canceled"
            : "free — subscribe to run bart"}
      </div>

      {phase === "just-subscribed" && (
        <div style={{ color: "var(--ok)", fontSize: 13, fontWeight: 600, marginBottom: 10 }}>
          ✓ thanks! your subscription is being confirmed…
        </div>
      )}

      {!gf && !active && (
        <>
          <div style={{ color: "var(--ink-mute)", fontSize: 14, lineHeight: 1.55, marginBottom: 14 }}>
            unlimited packets, cancel any time. payment is handled by stripe — we don't see your card.
          </div>
          <button className="run-btn" onClick={subscribe}
                  disabled={phase === "starting"}
                  style={{ padding: "12px 22px" }}>
            {phase === "starting" ? "redirecting…" : "subscribe — $10/month"}
          </button>
        </>
      )}

      {active && (
        <div style={{ marginTop: 8 }}>
          <div style={{ color: "var(--ink-mute)", fontSize: 14, lineHeight: 1.55, marginBottom: 12 }}>
            opens the stripe billing portal — update card, see invoices, or cancel.
          </div>
          <button onClick={manage}
                  disabled={phase === "opening-portal"}
                  style={{
                    padding: "10px 18px",
                    fontFamily: "var(--font-sans)",
                    fontWeight: 600,
                    fontSize: 14,
                    color: "var(--ink)",
                    background: "var(--cream)",
                    border: "0",
                    borderRadius: 999,
                    cursor: "pointer",
                    boxShadow: "inset 0 0 0 1.5px var(--cream-edge)",
                    transition: "box-shadow .14s ease, background .14s ease",
                  }}
                  onMouseEnter={e => { e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--ink-faint)"; }}
                  onMouseLeave={e => { e.currentTarget.style.boxShadow = "inset 0 0 0 1.5px var(--cream-edge)"; }}>
            {phase === "opening-portal" ? "opening stripe…" : "manage billing →"}
          </button>
          {billing.current_period_end && (
            <div className="muted mono" style={{ fontSize: 11, marginTop: 14, color: "var(--ink-faint)" }}>
              renews / ends {new Date(billing.current_period_end).toLocaleDateString()}
            </div>
          )}
        </div>
      )}

      {errMsg && (
        <div style={{ color: "var(--accent-lo)", fontSize: 13, marginTop: 10 }}>
          {errMsg}
        </div>
      )}
    </div>
  );
}

/* ─────────── Paywall modal (shown when /api/run returns 402) ─────────── */
function PaywallModal({ onClose }) {
  const [phase, setPhase] = useState("idle");
  const [errMsg, setErrMsg] = useState(null);

  async function subscribe() {
    setPhase("starting"); setErrMsg(null);
    try {
      const r = await fetch("/api/billing/checkout", { method: "POST", credentials: "same-origin" });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("error");
        setErrMsg(j.detail || `couldn't start checkout (${r.status})`);
        return;
      }
      const { url } = await r.json();
      window.location.href = url;
    } catch (e) {
      setPhase("error");
      setErrMsg(`network error: ${e.message}`);
    }
  }

  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, zIndex: 100,
      background: "rgba(34,30,25,0.55)",
      backdropFilter: "blur(2px)",
      display: "flex", alignItems: "center", justifyContent: "center",
      padding: 24,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: "min(440px, 100%)",
        background: "var(--cream-hi)",
        borderRadius: 18,
        boxShadow: "0 30px 60px rgba(34,30,25,0.25), inset 0 0 0 1px var(--cream-edge)",
        padding: "28px 28px 24px",
      }}>
        <div style={{
          fontFamily: "var(--font-mono)", fontSize: 10.5,
          color: "var(--ink-faint)", letterSpacing: "0.16em",
          textTransform: "uppercase", marginBottom: 6,
        }}>
          subscribe to run
        </div>
        <h3 style={{
          fontSize: 26, fontWeight: 700, color: "var(--ink)",
          letterSpacing: "-0.015em", lineHeight: 1.15, marginBottom: 12, marginTop: 0,
        }}>
          bart is <em style={{ fontStyle: "normal", color: "var(--accent)" }}>$10/month</em>
        </h3>
        <div style={{ color: "var(--ink-mute)", fontSize: 15, lineHeight: 1.55, marginBottom: 18 }}>
          unlimited packets. master plans, schematics, mnemonics, daily lessons, practice exams. cancel any time.
        </div>
        <button className="run-btn" onClick={subscribe}
                disabled={phase === "starting"}
                style={{ padding: "12px 22px", width: "100%" }}>
          {phase === "starting" ? "redirecting…" : "subscribe — $10/month"}
        </button>
        <div style={{ textAlign: "center", marginTop: 12 }}>
          <a href="#" onClick={e => { e.preventDefault(); onClose(); }}
             style={{
               fontFamily: "var(--font-mono)", fontSize: 11,
               color: "var(--ink-faint)", textDecoration: "none",
               letterSpacing: "0.08em",
             }}>
            maybe later
          </a>
        </div>
        {errMsg && (
          <div style={{ color: "var(--accent-lo)", fontSize: 13, marginTop: 10 }}>
            {errMsg}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─────────── Settings: change password ─────────── */
function ChangePasswordCard() {
  const [cur, setCur]   = useState("");
  const [nw, setNw]     = useState("");
  const [conf, setConf] = useState("");
  const [phase, setPhase] = useState("idle");   // idle | submitting | done | error
  const [errMsg, setErrMsg] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setErrMsg(null);
    if (nw.length < 6) { setErrMsg("new password must be at least 6 characters."); return; }
    if (nw !== conf)   { setErrMsg("new password and confirmation don't match."); return; }
    if (nw === cur)    { setErrMsg("new password is the same as the current one."); return; }
    setPhase("submitting");
    try {
      const r = await fetch("/api/auth/password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ current_password: cur, new_password: nw }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => ({}));
        setPhase("error");
        setErrMsg(j.detail || `couldn't update password (${r.status})`);
        return;
      }
      setPhase("done");
      setCur(""); setNw(""); setConf("");
    } catch (e2) {
      setPhase("error");
      setErrMsg(`network error: ${e2.message}`);
    }
  }

  const inputStyle = {
    flex: 1, padding: "10px 14px",
    background: "var(--cream)", border: "0", borderRadius: 10,
    fontFamily: "var(--font-mono)", fontSize: 13,
    boxShadow: "inset 0 0 0 1.5px var(--cream-edge)",
  };

  return (
    <div style={{
      marginTop: 18, padding: "20px 22px",
      background: "var(--cream-hi)",
      borderRadius: 14,
      boxShadow: "inset 0 0 0 1px var(--cream-edge)",
    }}>
      <div style={{
        fontFamily: "var(--font-mono)", fontSize: 10.5,
        color: "var(--ink-faint)", letterSpacing: "0.16em",
        textTransform: "uppercase", marginBottom: 8,
      }}>
        password
      </div>
      <div style={{
        color: "var(--ink-mute)", fontSize: 14, lineHeight: 1.55, marginBottom: 14,
      }}>
        change the password you use to log into bart. other devices you're signed in on will be logged out.
      </div>

      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <input type="password" placeholder="current password"
               value={cur} onChange={e => setCur(e.target.value)}
               autoComplete="current-password" style={inputStyle} />
        <input type="password" placeholder="new password (6+ characters)"
               value={nw} onChange={e => setNw(e.target.value)}
               autoComplete="new-password" style={inputStyle} />
        <input type="password" placeholder="confirm new password"
               value={conf} onChange={e => setConf(e.target.value)}
               autoComplete="new-password" style={inputStyle} />
        <div>
          <button type="submit"
                  className="run-btn"
                  style={{ padding: "10px 22px", fontSize: 14 }}
                  disabled={phase === "submitting" || !cur || !nw || !conf}>
            {phase === "submitting" ? "updating…" : "update password"}
          </button>
        </div>
      </form>

      {phase === "done" && (
        <div style={{ color: "var(--ok)", fontSize: 14, fontWeight: 600, marginTop: 10 }}>
          ✓ password updated.
        </div>
      )}
      {errMsg && (
        <div style={{ color: "var(--accent-lo)", fontSize: 13, marginTop: 10 }}>
          {errMsg}
        </div>
      )}
    </div>
  );
}

/* ─────────── Social: friends, favorites, leaderboard ─────────── */
function Social({ onBack, me }) {
  const [tab, setTab] = useState("friends");  // friends | favorites | leaderboard
  const [friends, setFriends]     = useState({ accepted: [], incoming: [], outgoing: [] });
  const [favorites, setFavorites] = useState([]);
  const [board, setBoard]         = useState([]);
  const [toast, setToast]         = useState(null);
  const [emailInput, setEmailInput] = useState("");
  const [shareInput, setShareInput] = useState("");

  function flash(msg) {
    setToast(msg);
    setTimeout(() => setToast(cur => cur === msg ? null : cur), 2400);
  }

  async function refresh() {
    const [f, fav, lb] = await Promise.all([
      fetch("/api/friends",     { credentials: "same-origin" }).then(r => r.ok ? r.json() : null),
      fetch("/api/favorites",   { credentials: "same-origin" }).then(r => r.ok ? r.json() : null),
      fetch("/api/leaderboard", { credentials: "same-origin" }).then(r => r.ok ? r.json() : null),
    ]);
    if (f)   setFriends(f);
    if (fav) setFavorites(fav.favorites || []);
    if (lb)  setBoard(lb.rows || []);
  }
  useEffect(() => { refresh(); }, []);

  async function sendRequest() {
    const email = emailInput.trim();
    if (!email) return;
    try {
      const r = await fetch("/api/friends/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { flash(j.detail || `couldn't send (${r.status})`); return; }
      setEmailInput("");
      flash(j.friendship?.status === "accepted"
        ? `you're already friends with ${email} — they requested you first.`
        : `request sent to ${email}.`);
      refresh();
    } catch (e) { flash(`network error: ${e.message}`); }
  }

  async function respondToRequest(friendshipId, accept) {
    try {
      const r = await fetch(`/api/friends/${friendshipId}/${accept ? "accept" : "decline"}`, {
        method: "POST", credentials: "same-origin",
      });
      if (!r.ok) { flash(`couldn't ${accept ? "accept" : "decline"} (${r.status})`); return; }
      flash(accept ? "friend added." : "request declined.");
      refresh();
    } catch (e) { flash(`network error: ${e.message}`); }
  }

  async function unfriend(otherId, email) {
    if (!confirm(`unfriend ${email}?`)) return;
    try {
      const r = await fetch(`/api/friends/${otherId}`, { method: "DELETE", credentials: "same-origin" });
      if (!r.ok) { flash(`couldn't unfriend (${r.status})`); return; }
      flash("unfriended.");
      refresh();
    } catch (e) { flash(`network error: ${e.message}`); }
  }

  async function saveFavorite() {
    const v = shareInput.trim();
    if (!v) return;
    try {
      const r = await fetch("/api/favorites", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify(/^https?:\/\//.test(v) || v.includes("/share/")
          ? { share_url: v } : { share_token: v }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { flash(j.detail || `couldn't save (${r.status})`); return; }
      setShareInput("");
      flash("saved to favorites.");
      refresh();
    } catch (e) { flash(`network error: ${e.message}`); }
  }

  async function removeFavorite(runId) {
    try {
      const r = await fetch(`/api/favorites/${runId}`, { method: "DELETE", credentials: "same-origin" });
      if (!r.ok) { flash(`couldn't remove (${r.status})`); return; }
      flash("removed.");
      refresh();
    } catch (e) { flash(`network error: ${e.message}`); }
  }

  function openShare(token) {
    if (!token) { flash("the owner revoked this share link."); return; }
    window.open(`/share/${token}`, "_blank", "noopener,noreferrer");
  }

  function fmtDateOnly(iso) {
    if (!iso) return "—";
    const d = new Date(iso.replace(" ", "T") + (iso.includes("Z") ? "" : "Z"));
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  const tabLink = (id, label, badge) => (
    <a href="#" onClick={e => { e.preventDefault(); setTab(id); }}
       style={{
         padding: "8px 14px",
         fontFamily: "var(--font-sans)", fontSize: 14, fontWeight: 600,
         color: tab === id ? "var(--accent)" : "var(--ink-mute)",
         borderBottom: tab === id ? "2px solid var(--accent)" : "2px solid transparent",
         textDecoration: "none", letterSpacing: "-0.005em",
       }}>
      {label}{badge ? <span style={{ marginLeft: 6, color: "var(--accent-lo)" }}>{badge}</span> : null}
    </a>
  );

  return (
    <div className="packet" style={{ marginTop: 24 }}>
      <div className="heading">
        friends
        <span className="pkt-cost" onClick={onBack} style={{ cursor: "pointer" }}>
          ← back to bart
        </span>
      </div>

      <div style={{
        display: "flex", gap: 6,
        borderBottom: "1px solid var(--cream-edge)",
        marginBottom: 22, marginTop: 4,
      }}>
        {tabLink("friends",     "friends",     friends.incoming.length || null)}
        {tabLink("favorites",   "favorites")}
        {tabLink("leaderboard", "leaderboard")}
      </div>

      {tab === "friends" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 22 }}>
            <input
              type="email" placeholder="friend's email"
              value={emailInput}
              onChange={e => setEmailInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") sendRequest(); }}
              style={{
                flex: 1, padding: "10px 14px",
                background: "var(--cream-hi)", border: "0", borderRadius: 10,
                fontFamily: "var(--font-sans)", fontSize: 14,
                boxShadow: "inset 0 0 0 1.5px var(--cream-edge)",
              }}
            />
            <button onClick={sendRequest} className="run-btn" style={{ padding: "10px 20px", fontSize: 14 }}>
              send request
            </button>
          </div>

          {friends.incoming.length > 0 && (
            <SectionList title="incoming requests">
              {friends.incoming.map(f => (
                <Row key={f.id}
                     left={
                       <>
                         {f.user.email}
                         <span style={{ color: "var(--ink-faint)", marginLeft: 8, fontSize: 13 }}>
                           sent {fmtDateOnly(f.since)}
                         </span>
                       </>
                     }
                     actions={
                       <>
                         <RowAction onClick={() => respondToRequest(f.id, true)}>accept</RowAction>
                         <RowAction muted onClick={() => respondToRequest(f.id, false)}>decline</RowAction>
                       </>
                     } />
              ))}
            </SectionList>
          )}

          <SectionList title={`your friends (${friends.accepted.length})`}>
            {friends.accepted.length === 0 && (
              <div className="muted mono" style={{ padding: "12px 6px" }}>
                no friends yet — invite someone by email above.
              </div>
            )}
            {friends.accepted.map(f => {
              // show name OR email, never both — keeps the row to one line.
              const display = (f.user.name && f.user.name.trim()) ? f.user.name : f.user.email;
              const sideNote = (f.user.name && f.user.name.trim()) ? f.user.email : null;
              return (
                <Row key={f.id}
                     left={
                       <>
                         <span style={{ fontWeight: 500 }}>{display}</span>
                         {sideNote && (
                           <span style={{ color: "var(--ink-mute)", marginLeft: 8, fontSize: 13 }}>
                             {sideNote}
                           </span>
                         )}
                         <span style={{ color: "var(--ink-faint)", marginLeft: 8, fontSize: 13 }}>
                           since {fmtDateOnly(f.since)}
                         </span>
                       </>
                     }
                     actions={
                       <RowAction muted onClick={() => unfriend(f.user.id, f.user.email)}>unfriend</RowAction>
                     } />
              );
            })}
          </SectionList>

          {friends.outgoing.length > 0 && (
            <SectionList title="pending (sent by you)">
              {friends.outgoing.map(f => (
                <Row key={f.id}
                     left={f.user.email}
                     actions={<span style={{ color: "var(--ink-faint)" }}>waiting…</span>} />
              ))}
            </SectionList>
          )}
        </div>
      )}

      {tab === "favorites" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 22 }}>
            <input
              type="text" placeholder="paste a /share/… link to save"
              value={shareInput}
              onChange={e => setShareInput(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") saveFavorite(); }}
              style={{
                flex: 1, padding: "10px 14px",
                background: "var(--cream-hi)", border: "0", borderRadius: 10,
                fontFamily: "var(--font-mono)", fontSize: 13,
                boxShadow: "inset 0 0 0 1.5px var(--cream-edge)",
              }}
            />
            <button onClick={saveFavorite} className="run-btn" style={{ padding: "10px 20px", fontSize: 14 }}>
              save
            </button>
          </div>

          {favorites.length === 0 ? (
            <div className="muted mono" style={{ padding: "20px 6px" }}>
              no favorites yet — ask a friend for a share link.
            </div>
          ) : (
            <div className="lesson-list">
              {favorites.map(f => (
                <div key={f.run_id} className="lesson-row" onClick={() => openShare(f.share_token)}>
                  <span className="num">{fmtDateOnly(f.saved_at)}</span>
                  <span className="title">
                    {f.saved_subject || <span className="muted">untitled</span>}
                    <span className="sub">from {f.owner_email}</span>
                  </span>
                  <span className="stat" style={{ color: f.share_token ? undefined : "var(--accent-lo)" }}>
                    {f.share_token ? "shared" : "revoked"}
                  </span>
                  <span className="open" onClick={e => { e.stopPropagation(); removeFavorite(f.run_id); }}>remove</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {tab === "leaderboard" && (
        <div>
          {board.length === 0 ? (
            <div className="muted mono" style={{ padding: "20px 6px" }}>
              loading… or add a friend on the friends tab to start a leaderboard.
            </div>
          ) : (
            <div className="lesson-list">
              {board.map((row, i) => (
                <div key={row.id} className="lesson-row"
                     style={row.is_you ? { background: "var(--accent-tint)" } : null}>
                  <span className="num">#{i + 1}</span>
                  <span className="title">
                    {row.is_you ? "you" : (row.name || row.email)}
                    <span className="sub">
                      {row.packets_created} {row.packets_created === 1 ? "packet" : "packets"}
                      {" · "}{row.files_uploaded} files
                      {" · "}{row.days_active} {row.days_active === 1 ? "day" : "days"} active
                    </span>
                  </span>
                  <span className="stat">
                    {row.last_active ? `last ${fmtDateOnly(row.last_active)}` : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {toast && (
        <div style={{
          position: "fixed", bottom: 28, left: "50%", transform: "translateX(-50%)",
          background: "var(--ink)", color: "var(--cream-hi)",
          padding: "10px 18px", borderRadius: 999,
          fontFamily: "var(--font-mono)", fontSize: 12.5, letterSpacing: 0.02,
          boxShadow: "0 10px 28px rgba(30,20,14,0.25)",
          zIndex: 100, maxWidth: 520, textAlign: "center",
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}

// small helpers used in Social
function SectionList({ title, children }) {
  return (
    <div style={{ marginBottom: 22 }}>
      <div style={{
        fontFamily: "var(--font-mono)", fontSize: 10.5,
        color: "var(--ink-faint)", letterSpacing: "0.16em",
        textTransform: "uppercase", marginBottom: 6,
      }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}
// One-line row: left content stays on a single line (truncates with …),
// actions sit on the right and are always visible.
function Row({ left, actions }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      gap: 16, padding: "10px 6px",
      borderBottom: "1px solid var(--cream-edge)",
      fontFamily: "var(--font-sans)",
    }}>
      <span style={{
        flex: 1, minWidth: 0,
        whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        fontSize: 14.5, color: "var(--ink)", letterSpacing: "-0.005em",
      }}>
        {left}
      </span>
      <div style={{
        display: "flex", gap: 14, alignItems: "center", flexShrink: 0,
        fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--accent)",
        letterSpacing: "0.04em", textTransform: "uppercase",
      }}>
        {actions}
      </div>
    </div>
  );
}
// Inline action chip used inside Row — looks like the existing .open links
// but always visible (the .open class hides them until the parent is hovered).
function RowAction({ onClick, muted, children }) {
  return (
    <a href="#" onClick={e => { e.preventDefault(); onClick && onClick(); }}
       style={{
         color: muted ? "var(--ink-faint)" : "var(--accent)",
         textDecoration: "none", cursor: "pointer",
       }}>
      {children}
    </a>
  );
}


/* ─────────── Past runs view ─────────── */
function PastRuns({ onBack, me }) {
  const [runs, setRuns] = useState(null);
  const [toast, setToast] = useState(null);  // string shown briefly after copy / share / unshare
  useEffect(() => {
    fetch("/api/runs")
      .then(r => r.ok ? r.json() : { runs: [] })
      .then(d => setRuns(d.runs || []))
      .catch(() => setRuns([]));
  }, []);

  function openRun(runId) {
    window.open(`/output/${runId}/index.html`, "_blank", "noopener,noreferrer");
  }
  function fmt(mtime) {
    const d = new Date(mtime * 1000);
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    }).toLowerCase();
  }

  function flash(msg) {
    setToast(msg);
    setTimeout(() => setToast(cur => cur === msg ? null : cur), 2200);
  }
  async function copyShareUrl(token) {
    const url = `${window.location.origin}/share/${token}`;
    try {
      await navigator.clipboard.writeText(url);
      flash("link copied — anyone with it can view this packet.");
    } catch (_) {
      flash(url);  // clipboard blocked — just show the URL so user can copy manually
    }
  }
  async function shareRun(runId) {
    try {
      const r = await fetch(`/api/runs/${runId}/share`, { method: "POST", credentials: "same-origin" });
      if (!r.ok) throw new Error(`share failed (${r.status})`);
      const { token } = await r.json();
      setRuns(prev => prev.map(p => p.run_id === runId ? { ...p, share_token: token } : p));
      copyShareUrl(token);
    } catch (e) {
      flash(`couldn't share: ${e.message}`);
    }
  }
  async function unshareRun(runId) {
    if (!confirm("revoke the share link? anyone using the current link will get a 404.")) return;
    try {
      const r = await fetch(`/api/runs/${runId}/share`, { method: "DELETE", credentials: "same-origin" });
      if (!r.ok) throw new Error(`unshare failed (${r.status})`);
      setRuns(prev => prev.map(p => p.run_id === runId ? { ...p, share_token: null } : p));
      flash("share link revoked.");
    } catch (e) {
      flash(`couldn't revoke: ${e.message}`);
    }
  }

  return (
    <div className="packet" style={{ marginTop: 24 }}>
      <div className="heading">
        past runs
        <span className="pkt-cost" onClick={onBack} style={{ cursor: "pointer" }}>
          ← back to bart
        </span>
      </div>

      {runs === null && (
        <div className="muted mono" style={{ padding: "20px 6px" }}>loading…</div>
      )}
      {runs !== null && runs.length === 0 && (
        <div className="muted mono" style={{ padding: "20px 6px" }}>
          no past runs yet for {me?.email || "this account"} — drop some files and let bart cook.
          {me?.email && (
            <div style={{ marginTop: 8, fontSize: 11 }}>
              if you ran bart on a different account, log out and back in with that email.
            </div>
          )}
        </div>
      )}
      {runs !== null && runs.length > 0 && (
        <div className="lesson-list">
          {runs.map(r => {
            const shared = !!r.share_token;
            return (
              <div key={r.run_id} className="lesson-row" onClick={() => openRun(r.run_id)}>
                <span className="num">{fmt(r.mtime)}</span>
                <span className="title">
                  {r.subject || <span className="muted">untitled</span>}
                  <span className="sub">{r.run_id}{shared ? " · shared" : ""}</span>
                </span>
                <span className="stat">
                  {r.sources.length} file{r.sources.length === 1 ? "" : "s"}
                </span>
                {shared ? (
                  <>
                    <span
                      className="open"
                      title="copy share link"
                      onClick={e => { e.stopPropagation(); copyShareUrl(r.share_token); }}
                    >copy link</span>
                    <span
                      className="open"
                      title="revoke the share link"
                      style={{ color: "var(--ink-faint)" }}
                      onClick={e => { e.stopPropagation(); unshareRun(r.run_id); }}
                    >unshare</span>
                  </>
                ) : (
                  <span
                    className="open"
                    title="create a public share link"
                    onClick={e => { e.stopPropagation(); shareRun(r.run_id); }}
                  >share ↗</span>
                )}
                <span className="open">open ↗</span>
              </div>
            );
          })}
        </div>
      )}

      {toast && (
        <div style={{
          position: "fixed", bottom: 28, left: "50%", transform: "translateX(-50%)",
          background: "var(--ink)", color: "var(--cream-hi)",
          padding: "10px 18px", borderRadius: 999,
          fontFamily: "var(--font-mono)", fontSize: 12.5, letterSpacing: 0.02,
          boxShadow: "0 10px 28px rgba(30,20,14,0.25)",
          zIndex: 100, maxWidth: 520, textAlign: "center",
        }}>
          {toast}
        </div>
      )}
    </div>
  );
}

/* ─────────── App ─────────── */
function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  useEffect(() => {
    document.documentElement.style.setProperty("--accent", t.accent);
  }, [t.accent]);

  // URL-synced view: refreshing on /app/settings keeps you on settings.
  // home → /app, runs → /app/runs, social → /app/friends, settings → /app/settings.
  const VIEW_TO_PATH = { home: "/app", runs: "/app/runs", social: "/app/friends", settings: "/app/settings" };
  const PATH_TO_VIEW = { "/app": "home", "/app/": "home", "/app/runs": "runs", "/app/friends": "social", "/app/settings": "settings" };
  const [view, _setView] = useState(() => PATH_TO_VIEW[window.location.pathname] || "home");
  const setView = (next) => {
    _setView(next);
    const path = VIEW_TO_PATH[next] || "/app";
    if (window.location.pathname !== path) {
      window.history.pushState({ view: next }, "", path);
    }
  };
  useEffect(() => {
    const onPop = () => _setView(PATH_TO_VIEW[window.location.pathname] || "home");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // current logged-in user — so the topbar can show which account you're on.
  // /app is gated server-side, so by the time we get here we have a session.
  const [me, setMe] = useState(null);
  // Premium (claude) run allowance for the month.
  const [usage, setUsage] = useState(null);
  // Whether the user's claude.ai account is connected. null = not yet checked,
  // true/false once the GET /api/auth/anthropic round-trip lands. We block the
  // "let bart cook" button on this so users don't hit a 401 mid-run.
  const [claudeConnected, setClaudeConnected] = useState(null);
  const refreshUsage = useCallback(() => {
    fetch("/api/usage", { credentials: "same-origin" })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setUsage(d))
      .catch(() => {});
  }, []);
  const refreshClaudeConnected = useCallback(() => {
    fetch("/api/auth/anthropic", { credentials: "same-origin" })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setClaudeConnected(!!d.connected); })
      .catch(() => {});
  }, []);
  // `has_run_access` is true when the user has an active subscription, a
  // trial credit, or is grandfathered. We block the gemma-download button
  // on it so non-subs don't burn the download bandwidth just to hit a paywall.
  const [hasRunAccess, setHasRunAccess] = useState(true);
  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(r => r.ok ? r.json() : null)
      .then(d => {
        if (d) {
          setMe(d.user);
          if (d.usage) setUsage(d.usage);
          if (typeof d.has_run_access === "boolean") setHasRunAccess(d.has_run_access);
        }
      })
      .catch(() => {});
    refreshClaudeConnected();
  }, [refreshClaudeConnected]);
  // Re-check connection state when the user lands back on the home view (e.g.
  // after coming back from /app/settings where they just connected).
  useEffect(() => {
    if (view === "home") refreshClaudeConnected();
  }, [view, refreshClaudeConnected]);

  // Browser-side Gemma (WebLLM) state — kept in sync with window.bartGemma,
  // which is defined by /gemma-browser.js (loaded as a module). bartGemma may
  // arrive slightly after this component mounts; we poll briefly so we don't
  // miss the subscription. The init effect that *triggers* downloads lives
  // below, after `model` is declared.
  const [gemmaState, setGemmaState] = useState({
    status: "idle", progress: 0, progressLabel: "",
    socketConnected: false, error: null, model: null,
  });
  useEffect(() => {
    let unsub = null;
    let cancelled = false;
    function subscribe() {
      if (cancelled) return;
      const g = window.bartGemma;
      if (g && typeof g.onChange === "function") {
        unsub = g.onChange(setGemmaState);
      } else {
        setTimeout(subscribe, 200);
      }
    }
    subscribe();
    return () => { cancelled = true; if (unsub) unsub(); };
  }, []);

  // user inputs
  const [subject, setSubject] = useState("");
  const [days,    setDays]    = useState(7);
  const [preset,  setPreset]  = useState("default");   // default | fast | turbo
  const [model,   setModel]   = useState("claude");    // claude | gemma-browser
  const [focus,   setFocus]   = useState("general — everything attached");

  // Browser-Gemma download is NOT auto-triggered when the user picks the
  // model — 1.4 GB shouldn't fly down the wire without explicit consent.
  // The "download gemma" button below calls window.bartGemma.init() instead.

  // files — upload immediately on drop so the run can start without a preamble.
  // Each entry carries: { id, name, size, raw, uploaded, uploading, error? }.
  const [files, setFiles] = useState([]);
  // in-flight upload promises so runPipeline can await them without polling
  const uploadsRef = useRef(new Set());

  function uploadOne(entry) {
    const fd = new FormData();
    fd.append("files", entry.raw, entry.name);
    const p = (async () => {
      try {
        const r = await fetch("/api/upload", { method: "POST", body: fd, credentials: "same-origin" });
        if (!r.ok) throw new Error(`upload failed (${r.status})`);
        setFiles(prev => prev.map(f => f.id === entry.id ? { ...f, uploading: false, uploaded: true } : f));
        return { ok: true, id: entry.id };
      } catch (e) {
        setFiles(prev => prev.map(f => f.id === entry.id ? { ...f, uploading: false, error: e.message } : f));
        return { ok: false, id: entry.id, error: e.message };
      } finally {
        uploadsRef.current.delete(p);
      }
    })();
    uploadsRef.current.add(p);
    return p;
  }

  function addFiles(list) {
    const next = list.map(f => ({
      id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2,6)}`,
      name: f.name, size: f.size, raw: f, uploaded: false, uploading: true,
    }));
    setFiles(prev => {
      const seen = new Set(prev.map(p => p.name + ":" + p.size));
      const merged = [...prev];
      const toUpload = [];
      for (const n of next) {
        if (!seen.has(n.name + ":" + n.size)) {
          merged.push(n);
          toUpload.push(n);
        }
      }
      toUpload.forEach(uploadOne);  // fire-and-forget; promises tracked in uploadsRef
      return merged;
    });
  }
  function removeFile(id) {
    // Capture the file so we know what to delete on the server. We drop the
    // chip optimistically — if the server delete fails we leave the chip
    // gone but the user can re-upload to overwrite. Only attempts the
    // network call once the upload has actually landed; chips that are
    // still mid-upload are just discarded locally (the upload promise
    // resolves into an orphan, which is fine — the next run's archive
    // step will sweep it up).
    let target = null;
    setFiles(prev => {
      target = prev.find(f => f.id === id) || null;
      return prev.filter(f => f.id !== id);
    });
    if (target && target.uploaded && target.name) {
      const name = encodeURIComponent(target.name);
      fetch(`/api/materials/${name}`, { method: "DELETE", credentials: "same-origin" })
        .catch(() => {});  // best-effort; archive at run-end is the safety net
    }
  }
  const over = useGlobalDrop(addFiles);

  // sync chips with whatever's actually on the server. Without this, leftovers
  // from an aborted or stale session sit in materials/users/<uid>/ silently
  // and get included in the next run — which is the "previous packet's file
  // shows up in this packet" bug. By surfacing them as chips on mount the
  // user sees what bart will use and can remove anything they don't want.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch("/api/materials", { credentials: "same-origin" });
        if (!r.ok) return;
        const j = await r.json();
        if (cancelled || !Array.isArray(j.files)) return;
        setFiles(prev => {
          const seen = new Set(prev.map(p => p.name + ":" + p.size));
          const additions = j.files
            .filter(f => !seen.has(f.name + ":" + f.size))
            .map(f => ({
              id: `srv-${f.name}-${f.size}`,
              name: f.name, size: f.size, raw: null,
              uploaded: true, uploading: false,
            }));
          return additions.length ? [...prev, ...additions] : prev;
        });
      } catch (_) {}
    })();
    return () => { cancelled = true; };
  }, []);

  // run state machine
  const [phase, setPhase] = useState("idle");  // idle | running | done
  const [msgs, setMsgs] = useState([]);
  const [pkt,  setPkt]  = useState(null);
  const esRef = useRef(null);

  // progress state — driven by bart-voice stage events. Each stage has a pct
  // floor; within a stage we tween toward the next stage's floor so the bar
  // never sits frozen, even if bart takes longer than expected. ETA is then
  // derived from elapsed-vs-pct, which stays honest as the run drags on.
  const [stageLabel, setStageLabel] = useState("warming up…");
  const [typedLabel, setTypedLabel] = useState("warming up…");
  const [stageIdx, setStageIdx]     = useState(-1);  // index into STAGES, -1 = pre-anything
  const [stageEnteredAt, setStageEnteredAt] = useState(null);
  const [nowTick, setNowTick]       = useState(Date.now());
  const [showDetails, setShowDetails] = useState(false);
  const [showPaywall, setShowPaywall] = useState(false);
  // real-progress signal from bart's stdout: orchestrator prints `[N/M] filename`
  // for each artifact it finishes. When this is set, it overrides stage tweens.
  // firstDoneAt + lastDoneAt + runStartedAt let us derive a per-artifact rate
  // that's stable between completions, so ETA ticks down and the bar tweens.
  const [artifacts, setArtifacts]   = useState({ done: 0, total: 0, firstDoneAt: null, lastDoneAt: null, runStartedAt: null });
  useEffect(() => {
    if (phase !== "running") return;
    const id = setInterval(() => setNowTick(Date.now()), 500);
    return () => clearInterval(id);
  }, [phase]);

  // typewriter: when stageLabel changes, retype it character-by-character so
  // it feels like bart is talking to you. Cancels the previous typing run if
  // a new line arrives mid-flight.
  useEffect(() => {
    let cancelled = false;
    setTypedLabel("");
    let i = 0;
    function tick() {
      if (cancelled) return;
      i += 1;
      setTypedLabel(stageLabel.slice(0, i));
      if (i < stageLabel.length) {
        // small jitter so punctuation feels natural, like real speech
        const ch = stageLabel.charCodeAt(i - 1);
        const isPunct = ch === 46 /* . */ || ch === 44 /* , */ || ch === 8212 /* — */;
        setTimeout(tick, isPunct ? 90 : 18);
      }
    }
    tick();
    return () => { cancelled = true; };
  }, [stageLabel]);

  function pctAndEta() {
    if (phase === "done") return { pct: 100, remaining: 0, label: null };

    // Preferred path: real artifact-completion signal from bart's stdout.
    // The artifact loop is ~85% of total wall time; we scale 10..95 across it
    // so the bar starts moving as soon as the first artifact finishes and
    // leaves room for the final rendering pass.
    if (artifacts.total > 0) {
      const { done, total, firstDoneAt, lastDoneAt, runStartedAt } = artifacts;
      let pct, remaining = null;

      if (done >= total) {
        // last artifact landed — we're rendering the packet now.
        pct = 95;
        remaining = 5;
      } else if (done === 0) {
        // bart's still extracting/planning. Hold at 10% so the bar is alive
        // but not lying — ETA is "estimating…" until the first artifact lands.
        pct = 10;
      } else {
        // Stable per-artifact rate, snapshotted at the last completion.
        // - done == 1 → use elapsed since run start (only one data point).
        // - done >= 2 → use the actual gap between firstDoneAt and lastDoneAt.
        const meanArtifactMs = done >= 2
          ? Math.max(1000, (lastDoneAt - firstDoneAt) / (done - 1))
          : Math.max(1000, (lastDoneAt - (runStartedAt || firstDoneAt)) || 1000);

        // Tween between done/total and (done+1)/total based on how far we've
        // gotten through the *current* artifact's expected runtime. Cap at
        // 95% of a slot so the bar never overshoots before the next completion.
        const sinceLast = nowTick - lastDoneAt;
        const slotProgress = Math.min(0.95, Math.max(0, sinceLast / meanArtifactMs));
        const ratio = Math.min(0.99, (done + slotProgress) / total);
        pct = Math.max(11, Math.min(94, Math.round(10 + ratio * 85)));

        // ETA: snapshot was (total - done) × mean at lastDoneAt; tick down by
        // however much time has passed since. Once we're 30% past expected,
        // stop pretending — switch to a "longer than usual" signal instead of
        // parking on a misleading "~2s remaining".
        const snapshotMs = (total - done) * meanArtifactMs;
        const remainingMs = snapshotMs - sinceLast;
        if (sinceLast > meanArtifactMs * 1.3) {
          remaining = "slow";   // sentinel — fmtRemaining renders "longer than usual…"
        } else {
          remaining = Math.max(2, Math.round(remainingMs / 1000));
        }
      }

      const label = done >= total
        ? "rendering your packet…"
        : `${done} of ${total} ${total === 1 ? "artifact" : "artifacts"} done`;
      return { pct, remaining, label };
    }

    // Fallback: pre-artifact phase (extracting, planning) — stage-floor tween.
    if (stageIdx < 0) return { pct: null, remaining: null, label: null };
    const cur = STAGES[stageIdx];
    const next = STAGES[stageIdx + 1];
    const inStage = Math.max(0, (nowTick - (stageEnteredAt || nowTick)) / 1000);
    let pct = cur.pct;
    if (next) {
      const span = next.pct - cur.pct;
      const ratio = Math.min(0.98, inStage / Math.max(15, cur.tweenSec || 60));
      pct = cur.pct + span * ratio;
    }
    pct = Math.max(1, Math.min(10, Math.round(pct)));  // pre-artifact phase caps at 10

    let remaining = Math.max(0, (cur.tweenSec || 0) - inStage);
    for (let i = stageIdx + 1; i < STAGES.length - 1; i++) {
      remaining += STAGES[i].tweenSec || 0;
    }
    remaining = Math.max(5, Math.round(remaining));
    return { pct, remaining, label: null };
  }
  function fmtRemaining(sec) {
    if (sec == null) return "estimating…";
    if (sec === "slow") return "longer than usual…";
    if (sec < 60) return `~${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    return s === 0 ? `~${m}m` : `~${m}m ${s}s`;
  }

  const fileInput = useRef(null);
  function onPick(e) {
    const fs = Array.from(e.target.files || []);
    if (fs.length) addFiles(fs);
    e.target.value = "";
  }

  const presetOpts = [
    { value: "default", label: "full quality",      desc: "opus · review on" },
    { value: "fast",    label: "fast",              desc: "sonnet · no review" },
    { value: "turbo",   label: "turbo",             desc: "haiku · parallel ×8" },
  ];

  // Visible model options on the hosted site:
  //   "claude"        — Claude Code, runs on the user's own claude.ai
  //                     subscription (free for Bart per-token)
  //   "gemma-browser" — Gemma 2 in the user's browser via WebLLM/WebGPU
  //
  // Hidden for now (uncomment + add a "claude-api" model branch in
  // /api/run when ready): a paid Anthropic API path that bills Bart per
  // token. That option only makes sense once we have a billing model that
  // accounts for it (today the $10/mo subscription assumes claude-code).
  //   { value: "claude-api", label: "claude (api)", desc: "anthropic api key · per-token cost" },
  // In-browser llama is admin-gated for now. The smallest model that fits
  // a typical laptop GPU (1B) can't reliably follow bart's structured
  // briefings — it tends to critique the prompt instead of executing it.
  // Surfacing it to regular users produces broken packets, so we keep it
  // visible only on the admin email until either a) the orchestrator grows
  // a simplified browser-model prompt path or b) we ship a different model.
  const ADMIN_EMAIL = "loctran0323@gmail.com";
  const isAdmin = (me?.email || "").toLowerCase() === ADMIN_EMAIL;
  const modelOpts = [
    { value: "claude",        label: "claude (account)", desc: "your claude.ai subscription · free" },
    ...(isAdmin ? [
      { value: "gemma-browser", label: "llama 3.2",      desc: "runs on your laptop · ~0.7 GB · lower quality than claude" },
    ] : []),
  ];

  function appendMsg(text, { system = true } = {}) {
    setMsgs(prev => [...prev, { id: prev.length, system, node: <>{text}</> }]);
  }

  /* run the real pipeline — uploads files, kicks off bart, streams stdout */
  async function runPipeline() {
    if (phase === "running") return;
    setPhase("running");
    setMsgs([]);
    setPkt(null);
    setStageLabel("warming up…");
    setStageIdx(-1);
    setStageEnteredAt(null);
    setArtifacts({ done: 0, total: 0, firstDoneAt: null, lastDoneAt: null, runStartedAt: Date.now() });
    setShowDetails(false);

    const safeDays = Number.isFinite(days) && days > 0 ? days : 7;

    // 1. drain any background uploads — files upload silently on drop, so we
    //    just wait for them to land. no chat noise here; the run kicks off
    //    the moment everything is on the server.
    if (uploadsRef.current.size > 0) {
      const results = await Promise.all(Array.from(uploadsRef.current));
      const failed = results.filter(r => !r.ok);
      if (failed.length > 0) {
        appendMsg(`${failed.length} upload(s) failed — drop them again.`);
        setPhase("idle");
        return;
      }
    }

    // 2. start the run
    let token;
    try {
      const rr = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, days: safeDays, focus, preset, model }),
      });
      if (rr.status === 402) {
        // Paywall — send them straight to /pricing. Tiny delay so the message
        // is readable before the redirect kicks in.
        const err = await rr.json().catch(() => ({}));
        appendMsg(err.detail || "subscribe to run bart — taking you to pricing…");
        setPhase("idle");
        setTimeout(() => { window.location.href = "/pricing"; }, 900);
        return;
      }
      if (rr.status === 429) {
        // Monthly premium-run allowance exhausted. Surface the message and
        // let the usage strip below the run-row show the reset date.
        const err = await rr.json().catch(() => ({}));
        appendMsg(err.detail || "you've used all your premium runs this month — your allowance resets on the 1st.");
        refreshUsage();
        setPhase("idle");
        return;
      }
      if (rr.status === 401) {
        // Claude account not connected — send them to settings.
        const err = await rr.json().catch(() => ({}));
        appendMsg(err.detail || "your claude account isn't connected — opening settings…");
        setClaudeConnected(false);
        setPhase("idle");
        setTimeout(() => setView("settings"), 700);
        return;
      }
      if (!rr.ok) {
        const err = await rr.json().catch(() => ({ detail: `run failed (${rr.status})` }));
        appendMsg(err.detail || `run failed (${rr.status})`);
        setPhase("idle");
        return;
      }
      ({ token } = await rr.json());
    } catch (e) {
      appendMsg(`could not reach the server: ${e.message}`);
      setPhase("idle");
      return;
    }

    // 3. stream bart's stdout — server tags each line with kind="bart" (his
    //    chatty voice, gets the loaf avatar) or kind="sys" (terminal mono).
    const es = new EventSource(`/api/events/${token}`);
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.kind === "eta") return;  // legacy; pct comes from artifacts now
        if (data.kind === "artifacts") {
          setArtifacts(prev => ({ ...prev, total: Math.max(prev.total, data.total || 0) }));
          return;
        }
        if (data.kind === "artifact_done") {
          const now = Date.now();
          setArtifacts(prev => ({
            ...prev,
            done: Math.max(prev.done, data.done || 0),
            total: Math.max(prev.total, data.total || 0),
            firstDoneAt: prev.firstDoneAt || now,
            lastDoneAt: now,
          }));
          setStageLabel(`finished ${data.file} — ${data.done}/${data.total}`);
          return;
        }
        if (typeof data.line === "string") {
          if (data.kind === "bart") {
            setStageLabel(data.line);
            const idx = findStageIdx(data.line);
            if (idx >= 0) {
              setStageIdx(prev => {
                if (idx > prev) {
                  setStageEnteredAt(Date.now());
                  return idx;
                }
                return prev;
              });
            }
          }
          appendMsg(data.line, { system: data.kind !== "bart" });
        }
      } catch (_) {}
    };
    es.addEventListener("done", () => {
      es.close();
      esRef.current = null;
      setPkt(buildPacket(subject, safeDays));
      setPhase("done");
      // A premium run just consumed an allowance slot — refresh the counter.
      refreshUsage();
      // The server archived materials/ into the run dir — clear the chips so
      // the next run starts clean.
      setFiles([]);
    });
    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (phase === "running") {
        appendMsg("stream disconnected — check the server log");
        setPhase("idle");
      }
    };
  }

  function cancel() {
    if (esRef.current) { esRef.current.close(); esRef.current = null; }
    setPhase("idle");
    setMsgs([]);
  }

  async function openLesson(target) {
    /* Real packet filenames produced by bart/render — discovered by listing
       output/ and picking the most recent run_<timestamp>/ directory.
       Top-file glyphs map to the 00_–04_ HTMLs the orchestrator writes. */
    const TOP_FILES = {
      MD: "00_master_plan.html",
      SC: "01_schematics.html",
      WN: "02_whimsical_notes.html",
      SS: "03_short_guide.html",
      PE: "04_practice_exam.html",
    };
    const leaf = target.kind === "day"
      ? `lessons/${target.id}.html`
      : (TOP_FILES[target.id] || "index.html");

    let runId = null;
    try {
      const r = await fetch("/output/", { headers: { Accept: "application/json" } });
      if (r.ok) {
        const j = await r.json();
        const runs = (j.files || [])
          .filter(f => f.type === "directory" && /^run_/.test(f.relative.replace(/^\/?|\/$/g, "")))
          .map(f => f.relative.replace(/^\/?|\/$/g, ""))
          .sort();
        runId = runs[runs.length - 1] || null;
      }
    } catch (_) { /* fall through to the offline notice */ }

    if (!runId) {
      alert(
        "no packet yet — your run is still building or didn't produce output.\n" +
        "check past runs in a moment."
      );
      return;
    }
    window.open(`/output/${runId}/${leaf}`, "_blank", "noopener,noreferrer");
  }

  const totalCost = useMemo(() => {
    // Gemma paths (server-local or browser) run on open weights — no per-token cost.
    if (model === "gemma" || model === "gemma-browser") return "$0";
    if (preset === "default") return "$4.80";
    if (preset === "fast")    return "$1.10";
    return "$0";
  }, [preset, model]);

  return (
    <>
      <header className="topbar">
        <Wordmark onClick={() => setView("home")} />
        <div className="right">
          {me && (
            <span className="mono" title="logged in as" style={{ color: "var(--ink-mute)" }}>
              {me.email}
            </span>
          )}
          <span className="mono">v0.4.2</span>
          <a href={view === "runs" ? "/app" : "/app/runs"}
             onClick={e => { e.preventDefault(); setView(view === "runs" ? "home" : "runs"); }}>
            {view === "runs" ? "bart" : "past runs"}
          </a>
          <a href={view === "social" ? "/app" : "/app/friends"}
             onClick={e => { e.preventDefault(); setView(view === "social" ? "home" : "social"); }}>
            {view === "social" ? "bart" : "friends"}
          </a>
          <a href={view === "settings" ? "/app" : "/app/settings"}
             onClick={e => { e.preventDefault(); setView(view === "settings" ? "home" : "settings"); }}>
            {view === "settings" ? "bart" : "settings"}
          </a>
          <a href="#" onClick={async e => {
            e.preventDefault();
            try {
              await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
            } catch (_) { /* still navigate even if the call fails */ }
            window.location.href = "/login";
          }}>log out</a>
        </div>
      </header>

      <main className="stage">
        <PettableBart />


        {view === "runs" ? (
          <PastRuns onBack={() => setView("home")} me={me} />
        ) : view === "social" ? (
          <Social onBack={() => setView("home")} me={me} />
        ) : view === "settings" ? (
          <Settings onBack={() => setView("home")} />
        ) : (<>

        <h1 className="bart-speech">
          drop your course materials in.<br/>
          i'll write your <em>study packet</em>.
        </h1>
        <div className="bart-sub">no setup. no terminal. just bart.</div>

        {/* drop box */}
        <label
          className={"drop-box" + (over ? " is-over" : "")}
        >
          {/* the <label> auto-forwards clicks to the <input> below — no
              explicit onClick needed. Adding one would double-fire the
              picker and cancel the first one. */}
          <span className="drop-glyph" aria-hidden="true">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
              <path d="M12 16V4M12 4l-4 4M12 4l4 4M5 20h14"
                stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </span>
          <span className="drop-title">
            drop your materials here<span style={{ color: "var(--ink-faint)", fontWeight: 500 }}>{" "}or{" "}</span><span className="pick">pick from your computer</span>
          </span>
          <span className="drop-meta">pdf · docx · pptx · md · txt</span>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept=".pdf,.docx,.pptx,.md,.txt"
            onChange={onPick}
          />
        </label>

        {/* file chips */}
        {files.length > 0 && (
          <div className="files">
            {files.map(f => (
              <span key={f.id} className="file-chip">
                <span className="icn">{glyphFor(extOf(f.name))}</span>
                <span>{f.name}</span>
                <span className="x" onClick={(e) => { e.stopPropagation(); removeFile(f.id); }}>×</span>
              </span>
            ))}
          </div>
        )}

        {/* run row — inline plain-language */}
        <div className="run-row">
          study for{" "}
          <WordInput
            value={subject}
            onChange={setSubject}
            placeholder="your subject"
          />
          {" "}over{" "}
          <WordInput
            value={String(days)}
            onChange={v => {
              const n = parseInt(v.replace(/[^0-9]/g, ""), 10);
              setDays(Number.isFinite(n) ? Math.max(1, Math.min(60, n)) : "");
            }}
            placeholder="7"
            size={3}
          />
          {" "}days at{" "}
          <WordSelect
            value={preset}
            onChange={setPreset}
            options={presetOpts}
          />
          {" "}on{" "}
          <WordSelect
            value={model}
            onChange={setModel}
            options={modelOpts}
          />
          .<br/>
          focus on{" "}
          <WordInput
            value={focus}
            onChange={setFocus}
            placeholder="anything weak / important"
          />
          .
          {model === "claude" && usage && !usage.unlimited && (
            <div className="run-usage">
              <b>{usage.remaining}</b> of {usage.limit} premium runs left this month
              {usage.remaining === 0 && <> · resets on the 1st</>}
            </div>
          )}
          {model === "claude" && claudeConnected === false && (
            <div className="run-usage" style={{
              marginTop: 10, padding: "10px 12px",
              background: "var(--accent-tint)", color: "var(--accent-lo)",
              borderRadius: 8, fontWeight: 500,
            }}>
              your claude account isn't connected — bart needs it to run.{" "}
              <span
                className="run-usage-cta"
                onClick={() => setView("settings")}
                style={{ cursor: "pointer", textDecoration: "underline" }}
              >
                connect claude account
              </span>
            </div>
          )}
          {model === "gemma-browser" && (
            <div className="run-usage" style={{
              marginTop: 10, padding: "10px 12px",
              background: "var(--accent-tint)", color: "var(--accent-lo)",
              borderRadius: 8, fontWeight: 500,
            }}>
              {gemmaState.status === "no-webgpu" && (
                <>this browser doesn't support webgpu — try chrome or edge on a laptop.</>
              )}
              {gemmaState.status === "error" && (
                <>llama failed to load: {gemmaState.error}</>
              )}
              {gemmaState.status === "idle" && (
                <>downloads once (~0.7 GB), then runs on your laptop — free, no claude account. quality is lower than claude.</>
              )}
              {gemmaState.status === "loading" && (
                <>
                  downloading llama 3.2 · <b>{Math.round((gemmaState.progress || 0) * 100)}%</b>
                  {gemmaState.progressLabel && <> · <span style={{ opacity: 0.75 }}>{gemmaState.progressLabel}</span></>}
                  <div style={{
                    marginTop: 6, height: 6, background: "rgba(0,0,0,0.08)",
                    borderRadius: 999, overflow: "hidden",
                  }}>
                    <div style={{
                      width: `${Math.max(2, Math.round((gemmaState.progress || 0) * 100))}%`,
                      height: "100%", background: "var(--accent)",
                      transition: "width 0.25s ease",
                    }} />
                  </div>
                </>
              )}
              {gemmaState.status === "ready" && (
                <>llama is loaded and ready — inference runs on your laptop, free.</>
              )}
              {gemmaState.status === "running" && (
                <>llama is working on the current step…</>
              )}
            </div>
          )}
        </div>

        <div className="run-cta">
          {phase !== "running" && (() => {
            // Decide the button label + click behaviour based on the model
            // and its readiness state. Three buckets:
            //   1. claude w/o connected account → route to settings
            //   2. gemma-browser not ready      → label reflects state, disabled
            //   3. otherwise                    → normal "let bart cook"
            const isBrowser = model === "gemma-browser";
            const needsClaudeConnect = model === "claude" && claudeConnected === false;
            const browserBlocking = isBrowser &&
              gemmaState.status !== "ready" &&
              gemmaState.status !== "running";

            let label, click, disabled = false;
            if (needsClaudeConnect) {
              label = "connect claude to run";
              click = () => setView("settings");
            } else if (browserBlocking) {
              // idle  → explicit "download gemma" button. clicking it kicks
              //         off init() which moves to "loading"
              // loading → show % progress, button disabled
              // error  → "retry download"
              // no-webgpu → terminal, disabled
              if (gemmaState.status === "no-webgpu") {
                label = "webgpu not supported";
                click = () => {};
                disabled = true;
              } else if (gemmaState.status === "error") {
                label = "retry download";
                click = () => window.bartGemma && window.bartGemma.init();
              } else if (gemmaState.status === "loading") {
                label = `downloading llama · ${Math.round((gemmaState.progress || 0) * 100)}%`;
                click = () => {};
                disabled = true;
              } else if (!hasRunAccess) {
                // Non-subscribers must subscribe before downloading — a
                // wasted download just to hit a paywall is brutal UX.
                label = "subscribe to use llama";
                click = () => { window.location.href = "/pricing"; };
              } else {
                // idle (or first paint before bartGemma reports)
                label = "download llama (0.7 GB)";
                click = () => window.bartGemma && window.bartGemma.init();
              }
            } else {
              label = phase === "done" ? "run again" : "let bart cook";
              click = runPipeline;
            }
            return (
              <button
                className="run-btn"
                onClick={click}
                disabled={disabled || phase === "running"}
              >
                {label}
                <svg className="arrow" viewBox="0 0 24 24" fill="none">
                  <path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor"
                        strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
            );
          })()}
          {phase === "running" && (
            <>
              <button className="run-btn" disabled>
                <span style={{ marginRight: 4 }}>baking</span>
                <span className="typing"><span></span><span></span><span></span></span>
              </button>
              <button className="run-secondary" onClick={cancel}>cancel</button>
            </>
          )}
        </div>

        {/* run errors — surfaces when an idle-state msg landed but no packet did */}
        {phase === "idle" && msgs.length > 0 && !pkt && (
          <div className="chat" style={{ paddingTop: 28 }}>
            <div className="heading" style={{ color: "var(--accent-lo)" }}>something went wrong</div>
            <div style={{
              marginTop: 10,
              padding: "10px 14px",
              background: "var(--cream-hi)",
              border: "1px solid var(--cream-edge)",
              borderRadius: 10,
              fontFamily: "var(--font-mono)",
              fontSize: 12, lineHeight: 1.55, color: "var(--ink-mute)",
              whiteSpace: "pre-wrap",
            }}>
              {msgs.slice(-12).map(m => (
                <div key={m.id} style={{ opacity: m.system ? 0.7 : 1 }}>
                  {!m.system && <span style={{ color: "var(--accent)" }}>bart: </span>}
                  {m.node}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* run progress — replaces the firehose chat-log */}
        {phase === "running" && (() => {
          const { pct, remaining } = pctAndEta();
          const hasArtifacts = artifacts.total > 0;
          return (
            <div className="chat" style={{ paddingTop: 28 }}>
              <div className="heading">bart is working</div>
              <div style={{
                margin: "18px 0 12px",
                fontFamily: "var(--font-sans)",
                fontSize: 16, lineHeight: 1.5,
                color: "var(--ink)",
                minHeight: 24,
              }}>
                {typedLabel}
                <span
                  aria-hidden="true"
                  style={{
                    display: "inline-block",
                    width: 2, height: "1em",
                    marginLeft: 2,
                    verticalAlign: "-2px",
                    background: "var(--accent)",
                    opacity: typedLabel === stageLabel ? 0 : 1,
                    animation: "bart-caret 0.9s steps(2) infinite",
                  }}
                />
              </div>
              <div style={{
                position: "relative",
                height: 8,
                background: "var(--cream-lo)",
                borderRadius: 999,
                overflow: "hidden",
                boxShadow: "inset 0 0 0 1px var(--cream-edge)",
              }}>
                <div style={{
                  position: "absolute", inset: 0,
                  width: pct == null ? "12%" : pct + "%",
                  background: "linear-gradient(90deg, var(--accent), var(--accent-hi))",
                  borderRadius: 999,
                  transition: "width 600ms ease",
                  animation: pct == null ? "indeterminate 1.6s ease-in-out infinite" : "none",
                }} />
              </div>
              <div style={{
                display: "flex", justifyContent: "space-between",
                marginTop: 10,
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                color: "var(--ink-mute)",
              }}>
                <span>
                  {pct == null ? "estimating…" : `${pct}%`}
                  {hasArtifacts && (
                    <span style={{ color: "var(--ink-faint)", marginLeft: 10 }}>
                      ({artifacts.done} of {artifacts.total} done)
                    </span>
                  )}
                </span>
                <span>{fmtRemaining(remaining)}{typeof remaining === "number" ? " remaining" : ""}</span>
              </div>
              <div style={{ marginTop: 16, textAlign: "right" }}>
                <a href="#" onClick={e => { e.preventDefault(); setShowDetails(v => !v); }}
                   style={{
                     fontFamily: "var(--font-mono)",
                     fontSize: 11, color: "var(--ink-faint)",
                     textDecoration: "none", letterSpacing: "0.08em",
                   }}>
                  {showDetails ? "hide details ▴" : "show details ▾"}
                </a>
              </div>
              {showDetails && (
                <div style={{
                  marginTop: 10,
                  maxHeight: 320, overflowY: "auto",
                  padding: "10px 14px",
                  background: "var(--cream-hi)",
                  border: "1px solid var(--cream-edge)",
                  borderRadius: 10,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11.5, lineHeight: 1.55,
                  color: "var(--ink-mute)",
                  whiteSpace: "pre-wrap",
                }}>
                  {msgs.slice(-200).map(m => (
                    <div key={m.id} style={{ opacity: m.system ? 0.75 : 1 }}>
                      {!m.system && <span style={{ color: "var(--accent)" }}>bart: </span>}
                      {m.node}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}

        {/* packet */}
        {pkt && (
          <Packet
            pkt={pkt}
            subject={subject}
            cost={totalCost}
            onOpen={openLesson}
          />
        )}

        <div className="footwisp">
          one bart<span className="dot">.</span> five agents<span className="dot">.</span> one packet<span className="dot">.</span>
        </div>

        </>)}
      </main>

      {/* full-window drop overlay */}
      <div className={"drop-overlay" + (over ? " is-on" : "")}>
        <div className="ring">
          <div className="big">drop them in</div>
          <div>bart will take it from here</div>
        </div>
      </div>

      <TweaksPanel title="Tweaks">
        <TweakSection title="Mood">
          <TweakColor
            label="Accent"
            value={t.accent}
            onChange={v => setTweak("accent", v)}
            options={["#c96442", "#a26c46", "#6e8b6e", "#4a6b8a", "#8d5a9a"]}
          />
        </TweakSection>
      </TweaksPanel>

      {showPaywall && <PaywallModal onClose={() => setShowPaywall(false)} />}
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
