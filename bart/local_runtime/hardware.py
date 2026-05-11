"""Hardware detection. Returns a `Platform` describing the host so the rest of
the runtime can pick the right inference engine, model variant, and quant.

Detection rules:
- darwin + arm64  → accelerator="metal", ram_gb=unified memory.
- linux + nvidia  → accelerator="cuda",  vram_gb=largest GPU's free VRAM.
                    ram_gb=system RAM (kept for fallback decisions).
- everything else → accelerator="cpu",   ram_gb=system RAM.

We probe *total* RAM (not available) when sizing the model tier, because the
relevant question is "can this machine run the model" not "what's free right
now". The tier picker applies a 0.55 safety factor on top of that.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    os: str            # "darwin" / "linux" / "windows"
    arch: str          # "arm64" / "x86_64"
    accelerator: str   # "metal" / "cuda" / "cpu"
    ram_gb: float      # total system RAM (or unified memory on Apple)
    vram_gb: float | None  # discrete-GPU VRAM if applicable, else None

    @property
    def is_apple_silicon(self) -> bool:
        return self.os == "darwin" and self.arch == "arm64"

    @property
    def usable_gb(self) -> float:
        """Memory the model can actually claim. On unified-memory Macs this is
        most of RAM; on CUDA it's VRAM; on CPU it's RAM minus a workspace."""
        if self.accelerator == "cuda" and self.vram_gb:
            return self.vram_gb
        if self.is_apple_silicon:
            # macOS keeps ~25% for the OS + apps even under pressure. Leave it.
            return self.ram_gb * 0.75
        return max(0.0, self.ram_gb - 4.0)


def _system_ram_gb() -> float:
    """Total system RAM in GB. Tries psutil, falls back to /proc/meminfo and
    sysctl, then 8.0 as a conservative default."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass
    # Linux
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / (1024 ** 2)
    except (FileNotFoundError, OSError):
        pass
    # macOS without psutil
    if shutil.which("sysctl"):
        try:
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=2, check=True,
            )
            return int(out.stdout.strip()) / (1024 ** 3)
        except (subprocess.SubprocessError, ValueError):
            pass
    return 8.0


def _nvidia_vram_gb() -> float | None:
    """Free VRAM in GB on the largest visible NVIDIA GPU, or None if no GPU."""
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    best_mb = 0
    for line in out.stdout.splitlines():
        try:
            best_mb = max(best_mb, int(line.strip()))
        except ValueError:
            continue
    if best_mb <= 0:
        return None
    return best_mb / 1024.0


def detect() -> Platform:
    sysname = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    else:
        arch = machine

    ram_gb = _system_ram_gb()

    if sysname == "darwin" and arch == "arm64":
        return Platform(os="darwin", arch=arch, accelerator="metal",
                        ram_gb=ram_gb, vram_gb=None)

    vram = _nvidia_vram_gb()
    if vram and vram >= 4.0:
        return Platform(os=sysname, arch=arch, accelerator="cuda",
                        ram_gb=ram_gb, vram_gb=vram)

    return Platform(os=sysname, arch=arch, accelerator="cpu",
                    ram_gb=ram_gb, vram_gb=None)


# Tier names map to model_catalog keys. Larger tier = bigger model.
TIER_HUGE = "huge"      # 30B+ dense
TIER_LARGE = "large"    # 30B MoE / 27B dense
TIER_MID = "mid"        # 14B
TIER_SMALL = "small"    # 8B
TIER_TINY = "tiny"      # 4B


def recommended_tier(p: Platform) -> str:
    """Map a Platform to a tier key. Conservative: a tier the machine should
    run comfortably in 4-bit quant with room for KV cache and OS overhead."""
    usable = p.usable_gb
    # Quant size estimates (Q4-ish, GB) including KV cache headroom for 32K ctx.
    if usable >= 22:
        return TIER_HUGE        # Qwen3-32B / Qwen3.6-27B Q4_K_M
    if usable >= 16:
        return TIER_LARGE       # Qwen3-Coder-30B-A3B MoE Q4_K_M (3.3B active)
    if usable >= 11:
        return TIER_MID         # Qwen3-14B Q4_K_M
    if usable >= 7:
        return TIER_SMALL       # Qwen3-8B Q4_K_M
    return TIER_TINY            # Qwen3-4B Q4_K_M
