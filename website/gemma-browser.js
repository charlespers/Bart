// gemma-browser.js — runs Gemma 2 inside the user's browser tab via WebLLM,
// and bridges completion requests from the server-side bart orchestrator
// over a WebSocket. Loaded as an ES module from bart.html; exposes its API
// on `window.bartGemma` so the Babel-JSX bart-app can use it.
//
// State machine (window.bartGemma.status):
//   "idle"        — not started; user hasn't picked Gemma yet
//   "no-webgpu"   — terminal: this browser/GPU doesn't support WebGPU
//   "loading"     — model download / compile in progress (progress in .progress)
//   "ready"       — model loaded, WebSocket connected, awaiting prompts
//   "running"     — currently doing an inference
//   "error"       — terminal until init() is called again (error in .error)
//
// The library is fetched on demand from a CDN — we don't bundle it, since
// the rest of the app is also CDN-loaded React/Babel.

// Pinning to a known-good range avoids surprise breakage from a future
// WebLLM release. esm.run resolves caret ranges, so this fetches the most
// recent 0.2.x at page-load time.
const WEBLLM_CDN = "https://esm.run/@mlc-ai/web-llm@^0.2.79";

// Llama 3.2 1B Instruct with 4-bit quantization. ~0.7 GB download. We
// stepped down from 3B after 3B inference dropped the WebGPU context
// ("external Instance reference no longer exists") on a low-VRAM laptop —
// 2 GB on disk fit fine but KV cache + activations during real inference
// blew past the GPU's working set. 1B is the smallest viable option that
// most consumer GPUs can sustain end-to-end. Output quality is limited —
// the model is too small to follow the orchestrator's structured briefings
// closely — so the in-browser path is best treated as a "free fallback"
// when claude isn't available rather than the recommended way to run bart.
// Weights cache in IndexedDB on first run.
const DEFAULT_MODEL_ID = "Llama-3.2-1B-Instruct-q4f16_1-MLC";
const DEFAULT_MODEL_DISPLAY = "Llama 3.2 (1B)";
const DEFAULT_MODEL_SIZE_GB = 0.7;
// Native window is 128K; we use 8K to match the BrowserBackend's prompt cap
// in bart/backends.py and keep VRAM use predictable on small GPUs. Raise
// both in lockstep if you want to feed more corpus through.
const DEFAULT_CONTEXT_WINDOW = 8192;

const listeners = new Set();
const state = {
  status: "idle",          // see state machine above
  model: null,             // currently-loaded model id
  modelDisplay: null,
  contextWindow: null,
  progress: 0,             // 0..1 during loading
  progressLabel: "",       // human-readable progress text from WebLLM
  error: null,             // string when status === "error" or "no-webgpu"
  socketConnected: false,
};
let engine = null;
let webllm = null;
let ws = null;
let wsReconnectTimer = null;

function emit() {
  // Pass a fresh shallow copy so React's Object.is comparison sees a new
  // reference and re-renders. Without this, listeners receive the same
  // object every time and React silently skips updates.
  const snapshot = { ...state };
  for (const fn of listeners) {
    try { fn(snapshot); } catch (e) { console.error("[bartGemma] listener threw:", e); }
  }
}

function setStatus(next, patch) {
  if (patch) Object.assign(state, patch);
  state.status = next;
  emit();
}

async function loadWebLLM() {
  if (webllm) return webllm;
  // Dynamic import the CDN ESM build. This is the only network call until
  // the model itself starts downloading.
  webllm = await import(/* @vite-ignore */ WEBLLM_CDN);
  return webllm;
}

async function hasWebGPU() {
  if (!navigator.gpu || typeof navigator.gpu.requestAdapter !== "function") {
    return false;
  }
  try {
    const adapter = await navigator.gpu.requestAdapter();
    return !!adapter;
  } catch (_) {
    return false;
  }
}

async function init({ model } = {}) {
  // Idempotent: if we're already loading or ready for this model, no-op.
  if ((state.status === "ready" || state.status === "loading") &&
      (!model || model === state.model)) {
    return;
  }
  state.error = null;
  state.progress = 0;
  state.progressLabel = "";

  if (!(await hasWebGPU())) {
    setStatus("no-webgpu", {
      error: "your browser doesn't support WebGPU. use chrome or edge on a laptop.",
    });
    return;
  }

  setStatus("loading", { progressLabel: "loading webllm…" });
  let lib;
  try {
    lib = await loadWebLLM();
  } catch (e) {
    setStatus("error", { error: `couldn't load webllm: ${e.message}` });
    return;
  }

  const modelId = model || DEFAULT_MODEL_ID;
  state.model = modelId;
  state.modelDisplay = DEFAULT_MODEL_DISPLAY;
  state.contextWindow = DEFAULT_CONTEXT_WINDOW;

  try {
    engine = await lib.CreateMLCEngine(
      modelId,
      {
        initProgressCallback: (report) => {
          // report: { progress: 0..1, text, timeElapsed }
          state.progress = Math.max(0, Math.min(1, report.progress || 0));
          state.progressLabel = (report.text || "").slice(0, 120);
          emit();
        },
      },
      {
        // Gemma 2's native window is 8K; WebLLM defaults to 4K which is too
        // tight for the orchestrator's distiller / author prompts. Bump it.
        context_window_size: DEFAULT_CONTEXT_WINDOW,
      },
    );
  } catch (e) {
    // WebGPU OOM, model not in registry, etc.
    setStatus("error", { error: `model load failed: ${e.message}` });
    return;
  }

  setStatus("ready", { progress: 1, progressLabel: "ready" });
  connectSocket();
}

function disconnectSocket() {
  if (wsReconnectTimer) { clearTimeout(wsReconnectTimer); wsReconnectTimer = null; }
  if (ws) {
    try { ws.close(); } catch (_) {}
    ws = null;
  }
  state.socketConnected = false;
  emit();
}

function connectSocket() {
  disconnectSocket();
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = `${scheme}//${window.location.host}/api/llm/socket`;
  let sock;
  try {
    sock = new WebSocket(url);
  } catch (e) {
    console.error("[bartGemma] WebSocket open failed:", e);
    scheduleReconnect();
    return;
  }
  ws = sock;

  sock.onopen = () => {
    state.socketConnected = true;
    sock.send(JSON.stringify({
      type: "ready",
      model: state.model,
      context_window: state.contextWindow,
    }));
    emit();
  };

  sock.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); }
    catch { return; }
    if (msg.type === "complete") {
      await handleCompletion(msg, sock);
    }
    // "pong" and other server-originated messages are ignored.
  };

  sock.onclose = () => {
    if (sock === ws) {
      state.socketConnected = false;
      emit();
      // Auto-reconnect while we're still in "ready" — covers transient drops
      // (laptop sleeps, wifi blip, server reload). Don't reconnect if the
      // engine was torn down.
      if (state.status === "ready" || state.status === "running") {
        scheduleReconnect();
      }
    }
  };

  sock.onerror = (e) => {
    console.warn("[bartGemma] WebSocket error:", e);
  };
}

function scheduleReconnect() {
  if (wsReconnectTimer) return;
  wsReconnectTimer = setTimeout(() => {
    wsReconnectTimer = null;
    if (state.status === "ready" || state.status === "running") connectSocket();
  }, 2000);
}

async function handleCompletion(msg, sock) {
  const requestId = msg.request_id;
  if (!engine) {
    safeSend(sock, {
      type: "completion_error",
      request_id: requestId,
      error: "engine_not_ready",
    });
    return;
  }
  setStatus("running");
  try {
    const reply = await engine.chat.completions.create({
      messages: msg.messages,
      max_tokens: Math.min(msg.max_tokens || 2048, 4096),
      temperature: typeof msg.temperature === "number" ? msg.temperature : 0.7,
      stream: false,
    });
    const text = reply?.choices?.[0]?.message?.content || "";
    const usage = reply?.usage || {};
    safeSend(sock, {
      type: "completion_done",
      request_id: requestId,
      text,
      usage: {
        prompt_tokens: usage.prompt_tokens || 0,
        completion_tokens: usage.completion_tokens || 0,
        total_tokens: usage.total_tokens || 0,
      },
    });
  } catch (e) {
    // Log the full error to devtools — categorisation below loses detail.
    console.error("[bartGemma] inference failed:", e);
    const errStr = (e && e.message) || String(e);
    // Map a few well-known errors so the Python side can react cleanly.
    // WebLLM's prompt-overflow errors say things like
    // "ContextWindowSizeExceededError" or "input length ... exceeds".
    let code = "inference_failed";
    if (/context.*(length|window|size)|too long|input.*exceed|prompt.*long|exceeds.*tokens/i.test(errStr)) {
      code = "context_too_long";
    } else if (/out of memory|OOM|allocation failed|webgpu.*lost|external instance.*no longer|device.*lost|instance.*destroyed/i.test(errStr)) {
      // WebGPU device/instance teardown almost always means VRAM pressure
      // forced the OS or driver to release the GPU context mid-inference.
      // We surface this as gpu_oom so the python side can hint at switching
      // to a smaller model or claude.
      code = "gpu_oom";
    }
    safeSend(sock, {
      type: "completion_error",
      request_id: requestId,
      error: code,
      detail: errStr.slice(0, 400),
    });
  } finally {
    if (state.status === "running") setStatus("ready");
  }
}

function safeSend(sock, obj) {
  if (!sock || sock.readyState !== WebSocket.OPEN) return;
  try { sock.send(JSON.stringify(obj)); } catch (_) {}
}

function onChange(fn) {
  listeners.add(fn);
  // Fire once synchronously with a fresh copy so the subscriber sees current
  // state immediately AND React detects the initial value as new.
  try { fn({ ...state }); } catch (_) {}
  return () => listeners.delete(fn);
}

function getState() {
  // Defensive copy so consumers don't mutate our state object.
  return { ...state };
}

// Public API. The Babel-JSX bart-app reaches in via window.bartGemma.
window.bartGemma = {
  init,
  getState,
  onChange,
  // Surfaced for the UI so it can show the right label without hardcoding.
  defaultModel: { id: DEFAULT_MODEL_ID, display: DEFAULT_MODEL_DISPLAY, sizeGB: DEFAULT_MODEL_SIZE_GB },
};
