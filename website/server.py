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
import uuid
import zipfile
from pathlib import Path

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

import auth


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MATERIALS_ROOT = ROOT / "materials" / "users"
OUTPUT_ROOT = ROOT / "output" / "users"
CONFIG_ROOT = ROOT / ".workspaces"
RUN_SCRIPT = ROOT / "run"
VENV_PY = ROOT / ".venv" / "bin" / "python"

MATERIALS_ROOT.mkdir(parents=True, exist_ok=True)
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
CONFIG_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="bart website")
auth.init_db()


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
async def signup(req: SignupRequest, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    if len(req.password) < 6:
        raise HTTPException(400, "password must be at least 6 characters.")
    if auth.find_user_by_email(req.email):
        raise HTTPException(409, "an account with that email already exists.")
    user_id = auth.create_user(req.email, req.password, req.name)
    sid = auth.create_session(user_id)
    auth.set_session_cookie(response, sid)
    row = auth.find_user_by_id(user_id)
    return {"user": _user_payload(row)}


@app.post("/api/auth/login")
async def login(req: LoginRequest, response: Response):
    if not _EMAIL_RE.match(req.email or ""):
        raise HTTPException(400, "that email doesn't look right.")
    row = auth.find_user_by_email(req.email)
    if not row or not auth.verify_password(req.password, row["password_hash"]):
        raise HTTPException(401, "email or password didn't match.")
    sid = auth.create_session(row["id"])
    auth.set_session_cookie(response, sid)
    return {"user": _user_payload(row)}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(auth.SESSION_COOKIE)
    if sid:
        auth.delete_session(sid)
    auth.clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user=Depends(auth.current_user)):
    return {"user": _user_payload(user)}


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


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), user=Depends(auth.current_user)):
    saved = []
    dest = _user_materials(user["id"])
    for f in files:
        if not f.filename:
            continue
        safe = Path(f.filename).name
        target = dest / safe
        with target.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append({"name": safe, "size": target.stat().st_size})
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
            if rc == 0:
                archived = _archive_materials_into_latest_run(meta["user_id"])
                run_id = archived[1] if archived else None
                if run_id is None:
                    latest = _latest_run_dir(meta["user_id"])
                    run_id = latest.name if latest else None
                if run_id:
                    auth.record_run(
                        run_id=run_id,
                        user_id=meta["user_id"],
                        subject=meta.get("subject", ""),
                        focus=meta.get("focus", ""),
                        preset=meta.get("preset", "default"),
                        days=meta.get("days", 7),
                        source_count=meta.get("source_count", 0),
                    )
                    await queue.put({"kind": "sys",
                                     "line": f"recorded run {run_id} for {meta['user_id']}"})
                await queue.put({"kind": "bart",
                                 "line": "all done! your packet is ready below — click any day to open it."})
            else:
                await queue.put({"kind": "sys",
                                 "line": f"[bart exited with code {rc}]"})
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
                item = await queue.get()
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

@app.get("/api/runs")
async def list_user_runs(user=Depends(auth.current_user)):
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


# ─── page routing ────────────────────────────────────────────────────────────

@app.get("/")
async def root_index():
    return FileResponse(HERE / "splash.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/login")
async def login_page():
    return FileResponse(HERE / "login.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/app")
async def app_index(user=Depends(auth.current_user_optional)):
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
