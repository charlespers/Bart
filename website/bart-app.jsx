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

/* ─────────── Past runs view ─────────── */
function PastRuns({ onBack, me }) {
  const [runs, setRuns] = useState(null);
  useEffect(() => {
    fetch("/api/runs")
      .then(r => r.ok ? r.json() : { runs: [] })
      .then(d => setRuns(d.runs || []))
      .catch(() => setRuns([]));
  }, []);

  function openRun(runId) {
    window.open(`output/${runId}/index.html`, "_blank", "noopener,noreferrer");
  }
  function fmt(mtime) {
    const d = new Date(mtime * 1000);
    return d.toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
    }).toLowerCase();
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
          {runs.map(r => (
            <div key={r.run_id} className="lesson-row" onClick={() => openRun(r.run_id)}>
              <span className="num">{fmt(r.mtime)}</span>
              <span className="title">
                {r.subject || <span className="muted">untitled</span>}
                <span className="sub">{r.run_id}</span>
              </span>
              <span className="stat">
                {r.sources.length} file{r.sources.length === 1 ? "" : "s"}
              </span>
              <span className="open">open ↗</span>
            </div>
          ))}
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

  const [view, setView] = useState("home");   // home | runs

  // current logged-in user — so the topbar can show which account you're on.
  // /app is gated server-side, so by the time we get here we have a session.
  const [me, setMe] = useState(null);
  useEffect(() => {
    fetch("/api/auth/me", { credentials: "same-origin" })
      .then(r => r.ok ? r.json() : null)
      .then(d => d && setMe(d.user))
      .catch(() => {});
  }, []);

  // user inputs
  const [subject, setSubject] = useState("");
  const [days,    setDays]    = useState(7);
  const [preset,  setPreset]  = useState("default");   // default | fast | turbo
  const [focus,   setFocus]   = useState("general — everything attached");

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
  function removeFile(id) { setFiles(prev => prev.filter(f => f.id !== id)); }
  const over = useGlobalDrop(addFiles);

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
  // real-progress signal from bart's stdout: orchestrator prints `[N/M] filename`
  // for each artifact it finishes. When this is set, it overrides stage tweens.
  const [artifacts, setArtifacts]   = useState({ done: 0, total: 0, firstDoneAt: null });
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
      const ratio = Math.min(1, artifacts.done / artifacts.total);
      const pct = Math.max(1, Math.min(99, Math.round(10 + ratio * 85)));
      // ETA: average seconds-per-artifact × remaining artifacts. Needs at
      // least one artifact done before we have a usable rate.
      let remaining = null;
      if (artifacts.done > 0 && artifacts.firstDoneAt) {
        const perArtifactMs = (nowTick - artifacts.firstDoneAt) / artifacts.done;
        const left = artifacts.total - artifacts.done;
        remaining = Math.max(5, Math.round((left * perArtifactMs) / 1000));
      }
      const label = artifacts.done >= artifacts.total
        ? "rendering your packet…"
        : `${artifacts.done} of ${artifacts.total} ${artifacts.total === 1 ? "artifact" : "artifacts"} done`;
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
    if (sec < 60) return `~${sec}s`;
    const m = Math.floor(sec / 60), s = sec % 60;
    return s === 0 ? `~${m}m` : `~${m}m ${s}s`;
  }

  const fileInput = useRef(null);
  function openFilePicker() { fileInput.current?.click(); }
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
    setArtifacts({ done: 0, total: 0, firstDoneAt: null });
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
        body: JSON.stringify({ subject, days: safeDays, focus, preset }),
      });
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
          setArtifacts(prev => ({
            done: Math.max(prev.done, data.done || 0),
            total: Math.max(prev.total, data.total || 0),
            firstDoneAt: prev.firstDoneAt || Date.now(),
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
      const r = await fetch("output/", { headers: { Accept: "application/json" } });
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
        "no packet yet — run ./run from the project root first.\n" +
        "(bart writes to output/run_<timestamp>/; this page reads from there)"
      );
      return;
    }
    window.open(`output/${runId}/${leaf}`, "_blank", "noopener,noreferrer");
  }

  const totalCost = useMemo(() => {
    if (preset === "default") return "$4.80";
    if (preset === "fast")    return "$1.10";
    return "$0";
  }, [preset]);

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
          <a href="#" onClick={e => { e.preventDefault(); setView(view === "runs" ? "home" : "runs"); }}>
            {view === "runs" ? "bart" : "past runs"}
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
        ) : (<>

        <h1 className="bart-speech">
          drop your course materials in.<br/>
          i'll write your <em>study packet</em>.
        </h1>
        <div className="bart-sub">no setup. no terminal. just bart.</div>

        {/* drop box */}
        <label
          className={"drop-box" + (over ? " is-over" : "")}
          onClick={openFilePicker}
        >
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
          .<br/>
          focus on{" "}
          <WordInput
            value={focus}
            onChange={setFocus}
            placeholder="anything weak / important"
          />
          .
        </div>

        <div className="run-cta">
          {phase !== "running" && (
            <button
              className="run-btn"
              onClick={runPipeline}
              disabled={phase === "running"}
            >
              {phase === "done" ? "run again" : "let bart cook"}
              <svg className="arrow" viewBox="0 0 24 24" fill="none">
                <path d="M5 12h14M13 5l7 7-7 7" stroke="currentColor"
                      strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          )}
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
                <span>{fmtRemaining(remaining)} remaining</span>
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
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
