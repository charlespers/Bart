"""bart website backend — serves splash/login/app and drives bart runs.

Auth (SQLite + cookie sessions):
  POST /api/auth/signup    create account + log in
  POST /api/auth/login     log in
  POST /api/auth/logout    log out
  GET  /api/auth/me        current user (or 401)

Static pages:
  GET  /                   splash.html (public)
  GET  /login              login.html  (public)
  GET  /app                bart.html   (requires session, else redirects)
  GET  /<file>             static files in website/

App API (require session):
  POST /api/upload         multipart files → materials/users/<uid>/
  POST /api/run            kicks off ./bart run with BART_MATERIALS / BART_OUTPUT
                           pointed at the user's workspace, runs serialized
                           with a global lock, records the run in DB on exit 0
  GET  /api/runs           DB-backed list filtered by user
  GET  /api/events/<tok>   SSE stream of subprocess stdout
  GET  /output/            JSON list of the user's own runs
  GET  /output/<run_id>/.. serves packet HTML — gated on run ownership
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

import auth

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MATERIALS_ROOT = ROOT / "materials" / "users"
OUTPUT_ROOT = ROOT / "output" / "users"
CONFIG_ROOT = ROOT / ".workspaces"
RUN_SCRIPT = ROOT / "run"
VENV_PY = ROOT / ".venv" / "bin" / "python"


def _user_claude_dir(user_id: int) -> Path:
    """Per-user CLAUDE_CONFIG_DIR — where `claude /login` writes credentials
    when we spawn it with this dir as $HOME or as CLAUDE_CONFIG_DIR."""
    p = CONFIG_ROOT / str(user_id) / ".claude"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _claude_connected(user_id: int) -> bool:
    """True when the per-user workspace has usable claude credentials.
    Claude Code 2.x writes ~/.claude.json after `claude auth login` completes.
    Older variants also wrote files into ~/.claude/, so we check both."""
    workspace = CONFIG_ROOT / str(user_id)
    if (workspace / ".claude.json").is_file():
        return True
    d = workspace / ".claude"
    if d.is_dir():
        for name in (".credentials.json", "credentials.json", "auth.json"):
            if (d / name).is_file():
                return True
    return False

MATERIALS_ROOT.mkdir(parents=True, exist_ok=True)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
CONFIG_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="bart website")
auth.init_db()


# Canonicalize: anyone hitting <app>.fly.dev gets 301'd to the studywithbart.com
# equivalent. /api/health is excluded so Fly's machine health checks don't
# follow the redirect and mark the app unhealthy.
@app.middleware("http")
async def redirect_to_canonical_host(request: Request, call_next):
    host = (request.headers.get("host") or "").lower().split(":")[0]
    if host.endswith(".fly.dev") and request.url.path != "/api/health":
        target = f"https://studywithbart.com{request.url.path}"
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(url=target, status_code=301)
    return await call_next(request)


# IP-based rate limiter for the public auth endpoints. Honors
# X-Forwarded-For when uvicorn is started with --proxy-headers (set in the
# Fly deployment); on localhost it just sees 127.0.0.1.
_limiter = Limiter(key_func=get_remote_address)
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ─── streaming + run state ───────────────────────────────────────────────────

_streams: dict[str, asyncio.Queue] = {}
_procs: dict[str, asyncio.subprocess.Process] = {}
_run_lock = asyncio.Lock()
# token -> {user_id, subject, focus, preset, days, materials_dir, output_dir}
_run_meta: dict[str, dict] = {}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
ETA_RE = re.compile(r"est\.?\s*time:\s*~?\s*(\d+(?:\.\d+)?)\s*(min(?:ute)?s?|m\b|sec(?:ond)?s?|s\b)", re.IGNORECASE)
ARTIFACTS_RE = re.compile(r"artifacts:\s*(\d+)", re.IGNORECASE)
# bart's orchestrator emits `✓ [N/M] filename.ext` after each artifact completes.
# (✗ for failed; we count both as a step done so progress doesn't stall.)
ARTIFACT_DONE_RE = re.compile(r"[✓✗]\s*\[(\d+)/(\d+)\]\s+(\S+)")
SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
BOX_CHARS = set("─│╭╮╯╰┌┐└┘├┤┬┴┼━┃┏┓┗┛┣┫┳┻╋")

BART_VOICE = [
    ("first-run setup",        "first time — i need to install my tools. takes about a minute."),
    ("Extracting materials",   "okay, peeking at your notes now…"),
    ("Building corpus",        "stitching everything together so i can read it as one stack."),
    ("warm-up check",          "quick sanity-call to the model — making sure auth works before we go big."),
    ("planning",               "figuring out how to budget the days. pinning the trickier topics earlier."),
    ("researcher",             "sent out the researcher agents to fan through your corpus in parallel."),
    ("drafting day",           "writing dense, with worked examples and quick-checks."),
    ("critic",                 "critic agent is grading the draft — i'll revise if it falls below threshold."),
    ("schematics",             "composing schematics — diagrams, formulae, and the traps you keep falling for."),
    ("whimsical notes",        "writing the mnemonics. these are the ones that actually stick."),
    ("short study guide",      "and the 60-minute version for the morning of."),
    ("practice exam",          "now the practice exam — a real 3-hour run with a key you can self-grade."),
    ("rendering html",         "almost there — rendering the html packet."),
    ("Run failed",             "uh oh — something broke. the system line below has the details."),
    ("No files in",            "i couldn't find your files in materials/. try dropping them again and re-running."),
    ("claude /login",          "you need to log into claude code first. open a terminal and run: claude /login"),
    ("rate limit",             "rate-limited by the subscription — i'll keep retrying for you."),
    ("finished with exit code 0", "all done! your packet is ready below. open day 1 first."),
]


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def _clean_line(raw: bytes) -> str | None:
    text = _strip_ansi(raw.decode("utf-8", errors="replace"))
    if "\r" in text:
        text = text.split("\r")[-1]
    text = text.rstrip()
    if not text.strip():
        return None
    stripped = text.strip()
    if all(c in SPINNER_CHARS + " " for c in stripped):
        return None
    if stripped and all(c in BOX_CHARS or c == " " for c in stripped):
        return None
    return text


def _bart_voice_for(text: str) -> str | None:
    low = text.lower()
    for needle, voice in BART_VOICE:
        if needle.lower() in low:
            return voice
    return None


# ─── per-user workspace ──────────────────────────────────────────────────────

def _user_materials(user_id: int) -> Path:
    p = MATERIALS_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_output(user_id: int) -> Path:
    p = OUTPUT_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _user_config_path(user_id: int) -> Path:
    p = CONFIG_ROOT / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p / ".bart_config.json"


def _latest_run_dir(user_id: int) -> Path | None:
    out = _user_output(user_id)
    runs = sorted(
        (d for d in out.iterdir() if d.is_dir() and d.name.startswith("run_")),
        key=lambda p: p.name,
    )
    return runs[-1] if runs else None


def _archive_materials_into_latest_run(user_id: int) -> tuple[int, str] | None:
    run = _latest_run_dir(user_id)
    if not run:
        return None
    src_root = _user_materials(user_id)
    sources = [p for p in src_root.iterdir() if p.is_file()]
    if not sources:
        return None
    dest = run / "_source"
    dest.mkdir(exist_ok=True)
    moved = 0
    for src in sources:
        try:
            shutil.move(str(src), str(dest / src.name))
            moved += 1
        except OSError:
            pass
    return (moved, run.name)


# ─── auth endpoints ──────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginRequest(BaseModel):
    email: str
    password: str


def _user_payload(row) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


@app.post("/api/auth/signup")
@_limiter.limit("3/hour")
async def signup(req: SignupRequest, request: Request, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    if len(req.password) < 6:
        raise HTTPException(400, "password must be at least 6 characters.")
    if auth.find_user_by_email(req.email):
        raise HTTPException(409, "an account with that email already exists.")
    user_id = auth.create_user(req.email, req.password, req.name)
    sid = auth.create_session(user_id)
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    row = auth.find_user_by_id(user_id)
    return {"user": _user_payload(row)}


@app.post("/api/auth/login")
@_limiter.limit("5/minute")
async def login(req: LoginRequest, request: Request, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    row = auth.find_user_by_email(req.email)
    if not row or not auth.verify_password(req.password, row["password_hash"]):
        raise HTTPException(401, "email or password didn't match.")
    sid = auth.create_session(row["id"])
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    return {"user": _user_payload(row)}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(auth.SESSION_COOKIE)
    if sid:
        auth.delete_session(sid)
    auth.clear_session_cookie(response)
    return {"ok": True}


class GoogleAuthRequest(BaseModel):
    credential: str   # the JWT id_token from Google Identity Services


@app.post("/api/auth/google")
@_limiter.limit("10/minute")
async def auth_google(req: GoogleAuthRequest, request: Request, response: Response):
    """Sign in (or sign up) with Google. Front end uses Google Identity Services
    to produce a signed JWT (id_token); we verify it against Google's public
    keys and check that the audience matches our client_id, then issue a
    session cookie."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "google sign-in isn't configured on the server.")
    # Lazy import so the package is only required if google auth is enabled.
    from google.oauth2 import id_token as google_id_token
    from google.auth.transport import requests as google_requests
    try:
        idinfo = google_id_token.verify_oauth2_token(
            req.credential, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except ValueError as e:
        raise HTTPException(401, f"google token couldn't be verified: {e}")

    if not idinfo.get("email_verified", False):
        raise HTTPException(401, "google says this email isn't verified.")

    google_sub = idinfo.get("sub")
    email = (idinfo.get("email") or "").strip().lower()
    name  = idinfo.get("name") or idinfo.get("given_name") or ""
    if not (google_sub and email):
        raise HTTPException(401, "google token missing required fields.")

    user = auth.find_or_create_google_user(google_sub, email, name)
    sid = auth.create_session(user["id"])
    auth.set_session_cookie(response, sid, secure=request.url.scheme == "https")
    return {"user": {"id": user["id"], "email": user["email"], "name": user["name"]}}


@app.get("/api/auth/google/config")
async def google_config():
    """Public endpoint — returns the GIS client_id so the login page can render
    the button without us having to template the HTML."""
    return {"client_id": GOOGLE_CLIENT_ID}


@app.get("/api/auth/me")
async def me(user=Depends(auth.current_user)):
    return {"user": _user_payload(user)}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/api/auth/password")
@_limiter.limit("5/minute")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user=Depends(auth.current_user),
):
    if len(req.new_password) < 6:
        raise HTTPException(400, "new password must be at least 6 characters.")
    if req.new_password == req.current_password:
        raise HTTPException(400, "new password is the same as the current one.")
    if not auth.verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(401, "current password didn't match.")
    auth.update_password(user["id"], req.new_password)
    # Keep this device logged in; kick every other session.
    current_sid = request.cookies.get(auth.SESSION_COOKIE)
    auth.delete_sessions_for_user(user["id"], keep_sid=current_sid)
    return {"ok": True}


# ─── health (public — useful for the splash to gauge config) ────────────────

@app.get("/api/health")
async def health():
    claude = shutil.which("claude")
    return {
        "ok": True,
        "claude_cli": claude,
        "claude_present": claude is not None,
    }


# ─── materials (auth) ────────────────────────────────────────────────────────

@app.get("/api/materials")
async def materials(user=Depends(auth.current_user)):
    d = _user_materials(user["id"])
    return {
        "files": [
            {"name": p.name, "size": p.stat().st_size}
            for p in d.iterdir() if p.is_file()
        ]
    }


MAX_UPLOAD_BYTES = 25 * 1024 * 1024     # 25 MB per file
MAX_USER_STORAGE = 250 * 1024 * 1024    # 250 MB per account
_UPLOAD_CHUNK = 64 * 1024


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), user=Depends(auth.current_user)):
    dest = _user_materials(user["id"])
    used = sum(p.stat().st_size for p in dest.iterdir() if p.is_file())
    saved = []
    for f in files:
        if not f.filename:
            continue
        safe = Path(f.filename).name
        target = dest / safe
        size = 0
        try:
            with target.open("wb") as out:
                while True:
                    chunk = await f.read(_UPLOAD_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            413, f"`{safe}` is too large — 25 MB max per file."
                        )
                    if used + size > MAX_USER_STORAGE:
                        raise HTTPException(
                            413, "you've hit the 250 MB storage limit. "
                                 "delete some files first.",
                        )
                    out.write(chunk)
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        used += size
        saved.append({"name": safe, "size": size})
    return {"saved": saved}


# ─── run (auth + global lock) ────────────────────────────────────────────────

class RunRequest(BaseModel):
    subject: str = ""
    days: int = 7
    focus: str = ""
    preset: str = "default"   # default | fast | turbo


@app.post("/api/run")
async def start_run(req: RunRequest, user=Depends(auth.current_user)):
    if _run_lock.locked():
        raise HTTPException(
            409, "bart is busy with another run — try again in a minute."
        )

    materials_dir = _user_materials(user["id"])
    sources = [p for p in materials_dir.iterdir() if p.is_file()]
    if not sources:
        raise HTTPException(
            400, "drop some files first, then click let bart cook."
        )

    if shutil.which("claude") is None:
        raise HTTPException(
            503,
            "the `claude` CLI isn't on PATH. install Claude Code from "
            "https://claude.ai/code, run `claude /login`, then try again.",
        )

    if not _claude_connected(user["id"]):
        raise HTTPException(
            401,
            "your claude account isn't connected — go to settings → connect.",
        )

    days = max(1, min(60, req.days))
    from datetime import date, timedelta
    exam = (date.today() + timedelta(days=days)).isoformat()

    config = {
        "auth_mode": "claude-code",
        "api_key": "",
        "exam_date": exam,
        "subject": (req.subject or "your course").strip()[:200],
        "guidance": (req.focus or "").strip(),
        "student_level": "undergraduate",
        "style": "academic-rigorous",
        "daily_hours": 3.0,
        "primary_model": "claude-opus-4-7",
        "daily_model": "claude-sonnet-4-6",
        "fast_model": "claude-haiku-4-5-20251001",
        "deep_research": False,
        "local_model_key": "",
    }
    config_path = _user_config_path(user["id"])
    config_path.write_text(json.dumps(config, indent=2))

    output_dir = _user_output(user["id"])

    if VENV_PY.exists():
        cmd = [str(VENV_PY), "-m", "bart", "run",
               "--max-parallel", "4", "--days", str(days)]
    else:
        cmd = [str(RUN_SCRIPT), "run",
               "--max-parallel", "4", "--days", str(days)]
    if req.preset == "fast":
        cmd.append("--fast")
    elif req.preset == "turbo":
        cmd.append("--turbo")

    token = uuid.uuid4().hex
    queue: asyncio.Queue = asyncio.Queue()
    _streams[token] = queue
    _run_meta[token] = {
        "user_id": user["id"],
        "subject": config["subject"],
        "focus": config["guidance"],
        "preset": req.preset,
        "days": days,
        "source_count": len(sources),
        "materials_dir": str(materials_dir),
        "output_dir": str(output_dir),
    }

    env = dict(os.environ)
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)
    # Per-user workspace — bart/paths.py picks these up at import time.
    env["BART_MATERIALS"] = str(materials_dir)
    env["BART_OUTPUT"] = str(output_dir)
    # Per-user claude credentials. HOME override means the spawned `claude`
    # CLI reads <workspace>/.claude/, not the host's ~/.claude/, so each user
    # runs against their own claude.ai session.
    env["HOME"] = str(CONFIG_ROOT / str(user["id"]))
    # Run from ROOT so `python -m bart` can import the bart package, and so
    # bart's .bart_config.json sits where the CLI expects it. The run lock
    # guarantees only one user owns ROOT/.bart_config.json at a time.
    cwd = ROOT
    (ROOT / ".bart_config.json").write_text(config_path.read_text())

    await _run_lock.acquire()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        _run_lock.release()
        raise HTTPException(500, f"could not launch bart: {e}")
    _procs[token] = proc

    async def reader():
        try:
            assert proc.stdout is not None
            seen_bart_voices: set[str] = set()
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = _clean_line(line)
                if text is None:
                    continue
                voice = _bart_voice_for(text)
                if voice and voice not in seen_bart_voices:
                    seen_bart_voices.add(voice)
                    await queue.put({"kind": "bart", "line": voice})
                m_eta = ETA_RE.search(text)
                if m_eta:
                    n = float(m_eta.group(1))
                    unit = m_eta.group(2).lower()
                    secs = int(n * 60) if unit.startswith("m") else int(n)
                    await queue.put({"kind": "eta", "seconds": secs})
                m_art = ARTIFACTS_RE.search(text)
                if m_art:
                    await queue.put({"kind": "artifacts", "total": int(m_art.group(1))})
                m_done = ARTIFACT_DONE_RE.search(text)
                if m_done:
                    await queue.put({
                        "kind": "artifact_done",
                        "done": int(m_done.group(1)),
                        "total": int(m_done.group(2)),
                        "file": m_done.group(3),
                    })
                await queue.put({"kind": "sys", "line": text})
            rc = await proc.wait()
            meta = _run_meta.get(token, {})
            user_id = meta.get("user_id")

            # Record the run if a run dir exists, regardless of exit code.
            # Bart may exit non-zero on partial failures (one artifact failed,
            # rate limit on the last step, etc.) but the artifacts on disk are
            # still useful — without a DB row they're invisible to /output/.
            run_id = None
            if user_id is not None:
                if rc == 0:
                    archived = _archive_materials_into_latest_run(user_id)
                    if archived:
                        run_id = archived[1]
                if run_id is None:
                    latest = _latest_run_dir(user_id)
                    run_id = latest.name if latest else None
                if run_id:
                    try:
                        auth.record_run(
                            run_id=run_id,
                            user_id=user_id,
                            subject=meta.get("subject", ""),
                            focus=meta.get("focus", ""),
                            preset=meta.get("preset", "default"),
                            days=meta.get("days", 7),
                            source_count=meta.get("source_count", 0),
                        )
                        print(f"[run-recorder] recorded {run_id} (user={user_id}, rc={rc})",
                              file=sys.stderr, flush=True)
                    except Exception as e:
                        print(f"[run-recorder] FAILED to record {run_id}: {e}",
                              file=sys.stderr, flush=True)

            if rc == 0:
                await queue.put({"kind": "bart",
                                 "line": "all done! your packet is ready below — click any day to open it."})
            else:
                await queue.put({"kind": "sys",
                                 "line": f"[bart exited with code {rc}] — partial output kept; check past runs"})
        except Exception as e:
            await queue.put({"kind": "sys", "line": f"[server error reading stdout: {e}]"})
        finally:
            await queue.put(None)
            _run_meta.pop(token, None)
            if _run_lock.locked():
                _run_lock.release()

    asyncio.create_task(reader())
    return {"token": token, "cmd": cmd}


@app.get("/api/events/{token}")
async def events(token: str, user=Depends(auth.current_user)):
    queue = _streams.get(token)
    if queue is None:
        raise HTTPException(404, "unknown or expired run token")
    meta = _run_meta.get(token)
    if meta and meta["user_id"] != user["id"]:
        raise HTTPException(403, "not your run")

    async def stream():
        try:
            while True:
                try:
                    # 25s timeout — under the typical 30-60s proxy idle cap.
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # SSE comment, ignored by EventSource
                    continue
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _streams.pop(token, None)
            proc = _procs.pop(token, None)
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass

    return StreamingResponse(stream(), media_type="text/event-stream")


# ─── runs list + packet serving (auth, ownership-gated) ──────────────────────

def _sync_orphan_runs(user_id: int) -> int:
    """Find run_* dirs on disk that aren't in the DB and back-fill them.
    Returns the number of runs added. Defensive against the recording path
    dropping a run (subprocess crash, server restart mid-run, etc.) — by the
    time the user opens past runs, every dir on disk should be visible.
    """
    out = _user_output(user_id)
    db_run_ids = {r["run_id"] for r in auth.list_runs(user_id)}
    added = 0
    for child in out.iterdir():
        if not (child.is_dir() and child.name.startswith("run_")):
            continue
        if child.name in db_run_ids:
            continue
        # Try to recover subject from bart's per-run config snapshot if present.
        cfg_path = child / ".bart_config.json"
        subject = ""
        if cfg_path.is_file():
            try:
                subject = json.loads(cfg_path.read_text()).get("subject", "") or ""
            except (OSError, ValueError):
                pass
        src_dir = child / "_source"
        source_count = sum(1 for _ in src_dir.iterdir()) if src_dir.is_dir() else 0
        try:
            auth.record_run(
                run_id=child.name,
                user_id=user_id,
                subject=subject,
                focus="",
                preset="default",
                days=7,
                source_count=source_count,
            )
            added += 1
        except Exception as e:
            print(f"[orphan-sync] failed to back-fill {child.name}: {e}",
                  file=sys.stderr, flush=True)
    if added:
        print(f"[orphan-sync] back-filled {added} run(s) for user={user_id}",
              file=sys.stderr, flush=True)
    return added


@app.get("/api/runs")
async def list_user_runs(user=Depends(auth.current_user)):
    _sync_orphan_runs(user["id"])
    rows = auth.list_runs(user["id"])
    out = _user_output(user["id"])
    enriched = []
    for r in rows:
        run_dir = out / r["run_id"]
        # mtime + sources kept for compatibility with the existing PastRuns UI
        try:
            mtime = run_dir.stat().st_mtime
        except OSError:
            mtime = None
        src_dir = run_dir / "_source"
        sources = (
            [p.name for p in src_dir.iterdir() if p.is_file()]
            if src_dir.is_dir() else []
        )
        enriched.append({**r, "mtime": mtime, "sources": sources})
    return {"runs": enriched}


@app.get("/output/")
async def list_output(user=Depends(auth.current_user)):
    entries = []
    out = _user_output(user["id"])
    for child in sorted(out.iterdir()):
        if child.is_dir() and child.name.startswith("run_"):
            entries.append({
                "type": "directory",
                "name": child.name,
                "relative": f"/{child.name}/",
            })
    return JSONResponse({"files": entries})


@app.post("/api/runs/{run_id}/share")
async def share_run(run_id: str, user=Depends(auth.current_user)):
    token = auth.create_share(run_id, user["id"])
    if not token:
        raise HTTPException(404, "run not found")
    return {"token": token, "url": f"/share/{token}"}


@app.delete("/api/runs/{run_id}/share")
async def unshare_run(run_id: str, user=Depends(auth.current_user)):
    if not auth.revoke_share(run_id, user["id"]):
        raise HTTPException(404, "run not found")
    return {"ok": True}


@app.get("/share/{token}")
async def share_root(token: str):
    row = auth.run_by_share_token(token)
    if not row:
        raise HTTPException(404, "this share link is invalid or was revoked.")
    return RedirectResponse(url=f"/share/{token}/index.html", status_code=302)


@app.get("/share/{token}/{path:path}")
async def serve_shared(token: str, path: str):
    row = auth.run_by_share_token(token)
    if not row:
        raise HTTPException(404, "this share link is invalid or was revoked.")
    base = _user_output(row["user_id"]) / row["run_id"]
    target = (base / path).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "bad path")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.get("/output/{run_id}/download.zip")
async def download_packet(run_id: str, user=Depends(auth.current_user)):
    owner = auth.run_owner(run_id)
    if owner is None or owner != user["id"]:
        raise HTTPException(404, "run not found")
    base = _user_output(owner) / run_id
    if not base.is_dir():
        raise HTTPException(404, "run dir missing on disk")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in base.rglob("*"):
            if path.is_file():
                z.write(path, arcname=str(path.relative_to(base)))
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'},
    )


@app.get("/output/{run_id}/{path:path}")
async def serve_packet(run_id: str, path: str, user=Depends(auth.current_user)):
    owner = auth.run_owner(run_id)
    if owner is None or owner != user["id"]:
        raise HTTPException(404, "run not found")
    base = _user_output(owner) / run_id
    target = (base / path).resolve()
    try:
        target.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(403, "bad path")
    if not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


@app.get("/output/{run_id}/")
async def serve_packet_index(run_id: str, user=Depends(auth.current_user)):
    return await serve_packet(run_id=run_id, path="index.html", user=user)


# ─── claude account integration ─────────────────────────────────────────────
#
# `claude /login` is a device-flow OAuth. We spawn it with HOME pointed at the
# per-user workspace dir so credentials land in <workspace>/.claude/ and stay
# isolated from other users. The CLI prints a URL like
#   `https://claude.ai/oauth/...code=ABCD-EFGH`
# (exact format varies by CLI version), then blocks until the user completes
# the flow in their browser. We capture the URL from stdout, surface it via
# SSE, and the run terminates when the user authorizes (or times out).

_CLAUDE_LOGIN_URL_RE = re.compile(
    r"(https?://(?:claude\.com|claude\.ai|platform\.claude\.com|console\.anthropic\.com|anthropic\.com|auth\.anthropic\.com)\S+)",
    re.IGNORECASE,
)
_claude_proc: dict[int, asyncio.subprocess.Process] = {}     # user_id -> proc
_claude_queue: dict[int, asyncio.Queue] = {}                  # user_id -> events


@app.get("/api/auth/anthropic")
async def anthropic_status(user=Depends(auth.current_user)):
    return {
        "connected": _claude_connected(user["id"]),
        "mode": "claude-code",
        "login_in_progress": user["id"] in _claude_proc,
    }


@app.post("/api/auth/anthropic/connect")
async def anthropic_connect(user=Depends(auth.current_user)):
    """Kick off `claude /login` for the current user. Returns a token the
    client uses to subscribe to /api/auth/anthropic/events for the device
    URL + completion."""
    if user["id"] in _claude_proc:
        raise HTTPException(409, "a connect flow is already in progress.")
    if shutil.which("claude") is None:
        raise HTTPException(
            503,
            "the `claude` CLI isn't installed on this server. "
            "see https://claude.ai/code to install it.",
        )

    user_id = user["id"]
    # Use HOME override so claude writes to <workspace>/.claude/ — isolated
    # from any other user (and from the host's own ~/.claude).
    workspace = CONFIG_ROOT / str(user_id)
    workspace.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(workspace)
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env.pop("ANTHROPIC_API_KEY", None)

    queue: asyncio.Queue = asyncio.Queue()
    _claude_queue[user_id] = queue

    try:
        proc = await asyncio.create_subprocess_exec(
            "claude", "auth", "login", "--claudeai",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except FileNotFoundError as e:
        _claude_queue.pop(user_id, None)
        raise HTTPException(500, f"couldn't launch claude auth login: {e}")
    _claude_proc[user_id] = proc

    async def reader():
        try:
            assert proc.stdout is not None
            url_seen = False
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = ANSI_RE.sub("", line.decode("utf-8", errors="replace")).rstrip()
                if not text.strip():
                    continue
                await queue.put({"kind": "log", "line": text})
                if not url_seen:
                    m = _CLAUDE_LOGIN_URL_RE.search(text)
                    if m:
                        url_seen = True
                        await queue.put({"kind": "url", "url": m.group(1)})
            rc = await proc.wait()
            if rc == 0 and _claude_connected(user_id):
                await queue.put({"kind": "ok"})
            else:
                await queue.put({"kind": "failed", "code": rc})
        except Exception as e:
            await queue.put({"kind": "failed", "error": str(e)})
        finally:
            await queue.put(None)
            _claude_proc.pop(user_id, None)

    asyncio.create_task(reader())
    return {"ok": True}


@app.get("/api/auth/anthropic/events")
async def anthropic_events(user=Depends(auth.current_user)):
    queue = _claude_queue.get(user["id"])
    if queue is None:
        raise HTTPException(404, "no connect flow in progress.")

    async def stream():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if item is None:
                    yield "event: done\ndata: {}\n\n"
                    break
                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _claude_queue.pop(user["id"], None)

    return StreamingResponse(stream(), media_type="text/event-stream")


class ClaudeCodeBody(BaseModel):
    code: str


@app.post("/api/auth/anthropic/code")
async def anthropic_paste_code(body: ClaudeCodeBody, user=Depends(auth.current_user)):
    """Forward the OAuth code the user pasted from claude.com back into the
    waiting `claude auth login` subprocess via stdin."""
    proc = _claude_proc.get(user["id"])
    if proc is None or proc.returncode is not None:
        raise HTTPException(409, "no active connect flow — click connect first.")
    if proc.stdin is None:
        raise HTTPException(500, "subprocess stdin is closed.")
    code = (body.code or "").strip()
    if not code:
        raise HTTPException(400, "paste the code you got after signing in on claude.com.")
    try:
        proc.stdin.write((code + "\n").encode("utf-8"))
        await proc.stdin.drain()
    except (BrokenPipeError, ConnectionResetError):
        raise HTTPException(500, "subprocess stopped accepting input.")
    return {"ok": True}


@app.delete("/api/auth/anthropic")
async def anthropic_disconnect(user=Depends(auth.current_user)):
    # kill any in-progress flow first
    proc = _claude_proc.pop(user["id"], None)
    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
    # wipe per-user credentials (file at workspace root + dir)
    workspace = CONFIG_ROOT / str(user["id"])
    creds_file = workspace / ".claude.json"
    if creds_file.is_file():
        try:
            creds_file.unlink()
        except OSError:
            pass
    d = workspace / ".claude"
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


# ─── friends ─────────────────────────────────────────────────────────────────

class FriendRequestBody(BaseModel):
    email: str


@app.get("/api/friends")
async def get_friends(user=Depends(auth.current_user)):
    return auth.list_friends(user["id"])


@app.post("/api/friends/request")
async def post_friend_request(req: FriendRequestBody, user=Depends(auth.current_user)):
    try:
        row = auth.send_friend_request(user["id"], req.email)
    except auth.FriendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "friendship": row}


@app.post("/api/friends/{friendship_id}/accept")
async def accept_friend(friendship_id: int, user=Depends(auth.current_user)):
    if not auth.accept_friend_request(friendship_id, user["id"]):
        raise HTTPException(404, "request not found or not yours to accept.")
    return {"ok": True}


@app.post("/api/friends/{friendship_id}/decline")
async def decline_friend(friendship_id: int, user=Depends(auth.current_user)):
    if not auth.decline_friend_request(friendship_id, user["id"]):
        raise HTTPException(404, "request not found or not yours to decline.")
    return {"ok": True}


@app.delete("/api/friends/{other_user_id}")
async def remove_friend(other_user_id: int, user=Depends(auth.current_user)):
    if not auth.unfriend(user["id"], other_user_id):
        raise HTTPException(404, "you aren't friends with that user.")
    return {"ok": True}


# ─── favorites ───────────────────────────────────────────────────────────────

class SaveFavoriteBody(BaseModel):
    share_token: Optional[str] = None
    share_url: Optional[str] = None


_SHARE_URL_RE = re.compile(r"/share/([A-Za-z0-9_\-]+)")


@app.get("/api/favorites")
async def get_favorites(user=Depends(auth.current_user)):
    return {"favorites": auth.list_favorites(user["id"])}


@app.post("/api/favorites")
async def save_favorite(body: SaveFavoriteBody, user=Depends(auth.current_user)):
    token = (body.share_token or "").strip()
    if not token and body.share_url:
        m = _SHARE_URL_RE.search(body.share_url)
        token = m.group(1) if m else ""
    if not token:
        raise HTTPException(400, "paste a /share/<token> link or token.")
    try:
        row = auth.save_favorite(user["id"], token)
    except auth.FriendError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "favorite": row}


@app.delete("/api/favorites/{run_id}")
async def remove_favorite(run_id: str, user=Depends(auth.current_user)):
    if not auth.remove_favorite(user["id"], run_id):
        raise HTTPException(404, "not in your favorites.")
    return {"ok": True}


# ─── leaderboard ─────────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
async def leaderboard(user=Depends(auth.current_user)):
    return {"rows": auth.leaderboard_for(user["id"])}


# ─── page routing ────────────────────────────────────────────────────────────

@app.get("/")
async def root_index():
    return FileResponse(HERE / "splash.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/login")
async def login_page():
    return FileResponse(HERE / "login.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


_APP_SUBVIEWS = {"", "runs", "friends", "settings"}


@app.get("/app")
@app.get("/app/")
@app.get("/app/{sub}")
async def app_index(sub: str = "", user=Depends(auth.current_user_optional)):
    if sub and sub not in _APP_SUBVIEWS:
        raise HTTPException(404)
    if user is None:
        return RedirectResponse(url="/login", status_code=302)
    return FileResponse(HERE / "bart.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


_NO_CACHE = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@app.get("/{filename:path}")
async def static_files(filename: str):
    # Hard-block lookups that escape the website dir or hit auth/db assets.
    if filename in {"bart.db", "auth.py", "server.py"} or filename.startswith(".."):
        raise HTTPException(403)
    target = (HERE / filename).resolve()
    try:
        target.relative_to(HERE)
    except ValueError:
        raise HTTPException(403)
    if target.is_file():
        return FileResponse(target, headers=_NO_CACHE)
    if target.with_suffix(".html").is_file():
        return FileResponse(target.with_suffix(".html"), headers=_NO_CACHE)
    raise HTTPException(404)
