"""Top-level entry point for the local runtime.

`prepare(cfg, console)` is the single call the orchestrator makes to bring
up everything: hardware detection, engine install, weights download,
server start, and a configured client. It returns a `RuntimeHandle` that
the LocalBackend wraps. Closing the handle stops the server and updates
the cache timestamps.

Designed to be safe to call once per orchestrator run; not designed for
multiple concurrent handles in the same process.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import cache as cache_mod
from . import installer
from . import server as server_mod
from .client import LocalClient
from .hardware import Platform, detect, recommended_tier
from .models import Model, get as get_model, pick


@dataclass
class RuntimeHandle:
    platform: Platform
    model: Model
    server: server_mod.Server
    client: LocalClient

    @property
    def is_llama_cpp(self) -> bool:
        return not self.model.is_mlx

    @property
    def context_window(self) -> int:
        return self.client.n_ctx

    @property
    def parallel_slots(self) -> int:
        return self.server.parallel_slots

    def shutdown(self) -> None:
        cache_mod.touch(self.model)
        self.server.shutdown()


def prepare(
    *,
    console,
    model_key: str | None = None,
    family: str = "auto",
    n_ctx: int = 32768,
    parallel_slots: int = 1,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> RuntimeHandle:
    """Bring up the local runtime end-to-end.

    `model_key`: override the auto-pick. None → detect hardware and choose.
    `family`: which open-weight family to auto-pick from when `model_key`
        is None — "qwen3", "gemma4", or "auto"/"" (→ the default, Qwen3).
        Ignored when `model_key` is given.
    `n_ctx`: server context window. Capped against model.context_window.
    `parallel_slots`: requested concurrent slots. mlx-lm forces 1.
    """
    platform = detect()
    if model_key:
        model = get_model(model_key)
    else:
        tier = recommended_tier(platform)
        model = pick(platform, tier, family=family)
    console.print(
        f"  [dim]hardware:[/dim] {platform.os}/{platform.arch} "
        f"[dim]{platform.accelerator}, "
        f"{platform.usable_gb:.0f} GB usable[/dim]"
    )
    console.print(
        f"  [dim]model:[/dim] [cyan]{model.display_name}[/cyan]"
    )

    cache_mod.evict_stale(on_evict=lambda k: console.print(
        f"  [dim]✓ evicted stale cached model {k}[/dim]"
    ))

    installer.ensure_engine(platform, console)
    installer.ensure_weights(model, console)

    eff_ctx = min(n_ctx, model.context_window)
    srv = server_mod.start(
        model, platform, n_ctx=eff_ctx, parallel_slots=parallel_slots,
    )
    console.print(
        f"  [green]✓[/green] local server ready at {srv.base_url} "
        f"[dim](slots={srv.parallel_slots}, n_ctx={eff_ctx})[/dim]"
    )

    client = LocalClient(
        base_url=srv.base_url,
        model_id=srv.model_id,
        n_ctx=eff_ctx,
        on_event=on_event,
    )
    return RuntimeHandle(platform=platform, model=model, server=srv, client=client)
