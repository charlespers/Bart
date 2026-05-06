"""Tests for the local-mode (Ollama / Gemma 4) lifecycle.

Covers the pure-Python pieces (tier picker, cache logic, payload shaping)
without requiring a running Ollama daemon. The HTTP-bound functions are
exercised in integration when the user actually runs `./run` in local mode.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bart import local_setup as ls


def test_pick_tier_high_end_picks_27b():
    primary, fast, label = ls.pick_tier(64.0)
    assert primary == "gemma3:27b"
    assert fast == "gemma3:1b"
    assert "27B" in label


def test_pick_tier_mid_range_picks_12b():
    primary, fast, _ = ls.pick_tier(20.0)
    assert primary == "gemma3:12b"
    assert fast == "gemma3:1b"


def test_pick_tier_laptop_picks_4b():
    primary, fast, _ = ls.pick_tier(8.0)
    assert primary == "gemma3:4b"


def test_pick_tier_minimal_picks_1b():
    primary, fast, label = ls.pick_tier(2.0)
    assert primary == "gemma3:1b"
    assert fast == "gemma3:1b"
    assert "1B" in label


def test_pick_tier_zero_memory_picks_smallest():
    primary, _, _ = ls.pick_tier(0.0)
    assert primary == "gemma3:1b"


# ── Registry validation ────────────────────────────────────────────


def test_model_exists_in_registry_returns_false_on_404(monkeypatch):
    """When the OCI registry returns 404 for a tag, the validator must
    refuse the model — this is the regression case for the user's
    `gemma4:1b` pull failure."""
    import subprocess

    class _FakeProc:
        def __init__(self): self.stdout = "404"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    assert ls.model_exists_in_registry("gemma9:99b") is False


def test_model_exists_in_registry_returns_true_on_200(monkeypatch):
    import subprocess

    class _FakeProc:
        def __init__(self): self.stdout = "200"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    assert ls.model_exists_in_registry("gemma3:12b") is True


def test_model_exists_in_registry_best_effort_on_network_error(monkeypatch):
    """When the registry is unreachable / curl errors, we don't want to
    block the user — let the actual pull surface any real error."""
    import subprocess

    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="curl", timeout=5)

    monkeypatch.setattr(subprocess, "run", _raise)
    assert ls.model_exists_in_registry("gemma3:12b") is True


def test_load_config_migrates_stale_gemma4_to_gemma3(tmp_path, monkeypatch):
    """Earlier builds saved `gemma4:Xb` model names which don't exist in
    Ollama's registry. load_config() should silently rewrite them to
    `gemma3:Xb` so existing users don't have to re-run setup."""
    import json
    from bart import config as cfg_mod

    fake_path = tmp_path / ".bart_config.json"
    fake_path.write_text(json.dumps({
        "auth_mode": "ollama-local",
        "api_key": "",
        "exam_date": "2099-01-01",
        "subject": "test",
        "guidance": "",
        "student_level": "undergraduate",
        "style": "academic-rigorous",
        "daily_hours": 3.0,
        "primary_model": "gemma4:12b",
        "fast_model": "gemma4:1b",
    }))
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", fake_path)
    cfg = cfg_mod.load_config()
    assert cfg is not None
    assert cfg.primary_model == "gemma3:12b"
    assert cfg.fast_model == "gemma3:1b"
    # Migration is persisted to disk so the next load is also clean.
    rewritten = json.loads(fake_path.read_text())
    assert rewritten["primary_model"] == "gemma3:12b"


def test_load_config_leaves_anthropic_models_alone(tmp_path, monkeypatch):
    """The migration must not touch Anthropic model names — only the local
    mode is affected."""
    import json
    from bart import config as cfg_mod

    fake_path = tmp_path / ".bart_config.json"
    fake_path.write_text(json.dumps({
        "auth_mode": "api",
        "api_key": "sk-ant-xxx",
        "exam_date": "2099-01-01",
        "subject": "test",
        "guidance": "",
        "student_level": "undergraduate",
        "style": "academic-rigorous",
        "daily_hours": 3.0,
        "primary_model": "claude-opus-4-7",
        "fast_model": "claude-haiku-4-5-20251001",
    }))
    monkeypatch.setattr(cfg_mod, "CONFIG_PATH", fake_path)
    cfg = cfg_mod.load_config()
    assert cfg is not None
    assert cfg.primary_model == "claude-opus-4-7"


def test_model_exists_in_registry_best_effort_when_curl_missing(monkeypatch):
    """If curl isn't on PATH, fall back to True so the actual pull runs."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    # Re-import the module-level `shutil` reference inside local_setup.
    monkeypatch.setattr(ls, "shutil", shutil)
    assert ls.model_exists_in_registry("gemma3:12b") is True


# ── Cache logic ─────────────────────────────────────────────────────


def _setup_cache(tmp_path, monkeypatch, contents: dict[str, str]):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(contents))
    monkeypatch.setattr(ls, "CACHE_PATH", cache_path)
    return cache_path


def test_cache_fresh_when_recent(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    _setup_cache(tmp_path, monkeypatch, {"gemma3:12b": now})
    assert ls.cache_is_fresh("gemma3:12b") is True


def test_cache_stale_when_over_24h(tmp_path, monkeypatch):
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    _setup_cache(tmp_path, monkeypatch, {"gemma3:12b": long_ago})
    assert ls.cache_is_fresh("gemma3:12b") is False


def test_cache_missing_model_is_not_fresh(tmp_path, monkeypatch):
    _setup_cache(tmp_path, monkeypatch, {})
    assert ls.cache_is_fresh("gemma3:12b") is False


def test_cache_corrupted_timestamp_is_not_fresh(tmp_path, monkeypatch):
    _setup_cache(tmp_path, monkeypatch, {"gemma3:12b": "not-a-date"})
    assert ls.cache_is_fresh("gemma3:12b") is False


def test_cache_touch_writes_iso_timestamp(tmp_path, monkeypatch):
    cache_path = _setup_cache(tmp_path, monkeypatch, {})
    ls.cache_touch("gemma3:4b")
    data = json.loads(cache_path.read_text())
    assert "gemma3:4b" in data
    # Should be parseable as ISO-format datetime.
    datetime.fromisoformat(data["gemma3:4b"])


def test_cache_evict_stale_removes_old_entries(tmp_path, monkeypatch):
    fresh = datetime.now(timezone.utc).isoformat()
    stale = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    cache_path = _setup_cache(tmp_path, monkeypatch, {
        "gemma3:12b": fresh,
        "gemma3:27b": stale,
    })
    # Stub out the HTTP delete so the test doesn't need a running daemon.
    monkeypatch.setattr(ls, "ollama_delete", lambda m: None)
    evicted = ls.cache_evict_stale()
    assert "gemma3:27b" in evicted
    assert "gemma3:12b" not in evicted
    remaining = json.loads(cache_path.read_text())
    assert "gemma3:12b" in remaining
    assert "gemma3:27b" not in remaining


def test_purge_all_clears_cache(tmp_path, monkeypatch):
    cache_path = _setup_cache(tmp_path, monkeypatch, {
        "gemma3:12b": datetime.now(timezone.utc).isoformat(),
        "gemma3:4b": datetime.now(timezone.utc).isoformat(),
    })
    deletions: list[str] = []
    monkeypatch.setattr(ls, "ollama_unload", lambda m: None)
    monkeypatch.setattr(ls, "ollama_delete", lambda m: deletions.append(m))
    removed = ls.purge_all_cached()
    assert set(removed) == {"gemma3:12b", "gemma3:4b"}
    assert set(deletions) == {"gemma3:12b", "gemma3:4b"}
    assert json.loads(cache_path.read_text()) == {}


# ── OllamaBackend payload construction (no HTTP) ────────────────────


def test_ollama_backend_flatten_string():
    from bart.backends import OllamaBackend
    assert OllamaBackend._flatten("hello") == "hello"


def test_ollama_backend_flatten_block_list():
    from bart.backends import OllamaBackend
    blocks = [
        {"type": "text", "text": "first"},
        {"type": "text", "text": "second"},
    ]
    out = OllamaBackend._flatten(blocks)
    assert "first" in out
    assert "second" in out


def test_ollama_backend_flatten_strips_cache_control():
    """cache_control blocks are an Anthropic-only concept — Ollama has
    no equivalent. They should be silently dropped."""
    from bart.backends import OllamaBackend
    blocks = [
        {"type": "text", "text": "kept", "cache_control": {"type": "ephemeral"}},
    ]
    out = OllamaBackend._flatten(blocks)
    assert out == "kept"  # text preserved; cache_control attribute irrelevant


def test_ollama_backend_sets_num_ctx_above_default(monkeypatch):
    """Ollama defaults num_ctx to 2048, which truncates bart's prompts and
    causes prompt-echo. The backend must compute a context budget large
    enough to fit the input + max_tokens with headroom. Floor is 16K
    even for small inputs because bart's full prompts always exceed
    the smaller Ollama buckets in practice."""
    import urllib.request
    from bart.backends import OllamaBackend
    from bart.telemetry import Telemetry

    captured = {}

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": "ok content here that's plenty long enough " * 30},
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: (captured.update(
                            body=json.loads(req.data.decode())) or _FakeResp()))
    backend = OllamaBackend(telemetry=Telemetry(), cache_dir=None)
    # Even a small input should hit the 16K floor.
    backend.complete(
        model="gemma3:12b",
        system="short",
        user="also short",
        max_tokens=1000,
        label="t",
        use_disk_cache=False,
    )
    assert captured["body"]["options"]["num_ctx"] >= 16384, (
        f"num_ctx floor not respected: {captured['body']['options']['num_ctx']}"
    )

    # Larger inputs should escalate to a bigger bucket.
    backend.complete(
        model="gemma3:12b",
        system="x" * 10_000,
        user="y" * 80_000,
        max_tokens=6000,
        label="t",
        use_disk_cache=False,
    )
    assert captured["body"]["options"]["num_ctx"] >= 32768


def test_ollama_backend_num_ctx_env_override(monkeypatch):
    import urllib.request
    from bart.backends import OllamaBackend
    from bart.telemetry import Telemetry

    captured = {}

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": "x" * 1000},
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    monkeypatch.setenv("BART_OLLAMA_NUM_CTX", "4096")
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: (captured.update(
                            body=json.loads(req.data.decode())) or _FakeResp()))
    backend = OllamaBackend(telemetry=Telemetry(), cache_dir=None)
    backend.complete(
        model="gemma3:1b", system="s", user="u",
        max_tokens=1000, label="t", use_disk_cache=False,
    )
    assert captured["body"]["options"]["num_ctx"] == 4096


def test_input_overlap_ratio_low_for_original_synthesis():
    from bart.backends import _input_overlap_ratio
    inp = "Subject: ECE 201\n\nThe corpus discusses Fourier transforms, " \
          "convolution, sampling theorem, and z-transforms in detail."
    out = "Subject: ECE 201 notation\n\n- $x(t)$ for time signals\n" \
          "- $H(\\omega)$ for frequency response\n- $\\delta$ for impulse"
    ratio = _input_overlap_ratio(out, inp, chunk=40)
    assert ratio < 0.3, f"original output flagged as echo: {ratio}"


def test_input_overlap_ratio_high_for_verbatim_echo():
    from bart.backends import _input_overlap_ratio
    inp = "ARTIFACT: daily_lesson\nTEACHING CONTRACT — please write a lesson covering Fourier transforms, convolution, and sampling. OBJECTIVES include mastering all properties."
    out = inp[:160]  # straight verbatim copy
    ratio = _input_overlap_ratio(out, inp, chunk=40)
    assert ratio >= 0.9, f"verbatim echo not detected: {ratio}"


def test_input_overlap_ratio_handles_empty_inputs():
    from bart.backends import _input_overlap_ratio
    assert _input_overlap_ratio("", "anything") == 0.0
    assert _input_overlap_ratio("anything", "") == 0.0


def test_ollama_backend_detects_prompt_echo_via_overlap(monkeypatch):
    """When the model regurgitates the prompt verbatim (high byte overlap
    with input AND short output), the backend should raise LLMError after
    failing to recover via retry."""
    import urllib.request
    from bart.backends import OllamaBackend, LLMError
    from bart.telemetry import Telemetry

    user_msg = (
        "ARTIFACT: daily_lesson\n"
        "Subject: ECE 201\n\n"
        "BRIEF\n"
        "TEACHING CONTRACT — write a lesson with the following sections.\n"
        "OBJECTIVES\n- master Fourier transforms and their properties\n"
        "SKELETON (follow this exactly)\n"
        "## Recap\n## New material\n## Practice\n"
        "Fill in each section with detailed content drawn from the corpus."
    )

    # Both the initial call and the retry get the same echoed text — so
    # the overlap check fires twice and the LLMError lands.
    echo_text = user_msg[:200]  # first 200 chars of the prompt, copied

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": echo_text},
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp())
    backend = OllamaBackend(telemetry=Telemetry(), cache_dir=None)
    with pytest.raises(LLMError, match="echoing the prompt"):
        backend.complete(
            model="gemma3:1b", system="x", user=user_msg,
            max_tokens=4000, label="daily", use_disk_cache=False,
        )


def test_ollama_backend_does_not_falsely_detect_echo_for_short_legit_output(monkeypatch):
    """Regression: notation_extractor-style legit output (~150-300 chars,
    mentions 'Subject:' once because that's how it formats its header)
    must NOT trip the echo guard. The previous anchor-based detector
    fired here; the new overlap detector does not."""
    import urllib.request
    from bart.backends import OllamaBackend
    from bart.telemetry import Telemetry

    user_msg = "Subject: ECE 201\n\nCORPUS BRIEF\n" + ("brief content here " * 500)
    # Legit output: short, original synthesis. Mentions "Subject:" but doesn't
    # copy meaningful chunks from the input.
    legit_output = (
        "Subject: ECE 201 notation\n\n"
        "- $x(t)$ for continuous-time signals\n"
        "- $x[n]$ for discrete-time\n"
        "- $H(\\omega)$ for the frequency response\n"
        "- $\\delta(t)$ for the Dirac impulse"
    )

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": legit_output},
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=None: _FakeResp())
    backend = OllamaBackend(telemetry=Telemetry(), cache_dir=None)
    out = backend.complete(
        model="gemma3:1b", system="x", user=user_msg,
        max_tokens=600, label="notation_extractor", use_disk_cache=False,
    )
    # Must succeed, must return the legit output.
    assert "Dirac" in out


def test_ollama_backend_retries_on_echo_and_recovers(monkeypatch):
    """When the first call echoes the prompt, the backend should retry
    once with a stripped prompt. If the retry returns a non-echo answer,
    we accept it and don't raise."""
    import urllib.request
    from bart.backends import OllamaBackend
    from bart.telemetry import Telemetry

    user_msg = "Subject: test\n\nBRIEF\n" + ("blah blah content here " * 200)
    call_count = {"n": 0}

    class _FakeResp1:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": user_msg[:300]},  # echo
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    class _FakeResp2:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": "Real answer that synthesizes information " * 30},
                "prompt_eval_count": 1, "eval_count": 1,
            }).encode()

    def _fake(req, timeout=None):
        call_count["n"] += 1
        return _FakeResp1() if call_count["n"] == 1 else _FakeResp2()

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    backend = OllamaBackend(telemetry=Telemetry(), cache_dir=None)
    out = backend.complete(
        model="gemma3:1b", system="x", user=user_msg,
        max_tokens=2000, label="t", use_disk_cache=False,
    )
    assert "Real answer" in out
    assert call_count["n"] == 2  # initial + 1 retry


def test_ollama_backend_complete_round_trips_via_http(monkeypatch):
    """Stub the urllib call and verify the request payload matches the
    Ollama /api/chat schema and the response gets parsed correctly."""
    import urllib.request
    from bart.backends import OllamaBackend
    from bart.telemetry import Telemetry

    captured = {}

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self):
            return json.dumps({
                "message": {"content": "hello back"},
                "prompt_eval_count": 50,
                "eval_count": 12,
            }).encode()

    def _fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    tel = Telemetry()
    backend = OllamaBackend(telemetry=tel, cache_dir=None)
    out = backend.complete(
        model="gemma3:12b",
        system="be helpful",
        user="hi",
        max_tokens=200,
        label="t",
        use_disk_cache=False,
    )
    assert out == "hello back"
    assert "/api/chat" in captured["url"]
    body = captured["body"]
    assert body["model"] == "gemma3:12b"
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    assert body["options"]["num_predict"] == 200
    # Telemetry should have recorded the call with reported token counts.
    assert tel.calls[0].input_tokens == 50
    assert tel.calls[0].output_tokens == 12
    assert tel.calls[0].cost_usd == 0.0
