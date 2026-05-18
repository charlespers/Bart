"""``bart-figure`` — a custom, generated illustration.

The Author asks for an image in plain language (``"prompt": "a labeled
diagram of the water cycle"``) and bart turns it into a real figure.

When image generation is enabled for the run (``BART_IMAGE_GEN=1``, set by
the orchestrator from ``cfg.image_generation``), the prompt is sent to an
open-source image backend (see ``bart.imagegen``) and the result is embedded
as a self-contained ``data:`` URI — the packet stays portable.

When it is disabled, or when generation fails, the block renders a tasteful
placeholder card naming the requested illustration, so the packet is always
complete and the build never blocks on the network.

Internal to ``bart.render.blocks``.
"""
from __future__ import annotations

from html import escape as _esc


# Aspect ratio → (width, height) in pixels for the generator request.
_ASPECTS: dict[str, tuple[int, int]] = {
    "16:9": (768, 432),
    "3:2":  (768, 512),
    "4:3":  (768, 576),
    "1:1":  (640, 640),
    "2:3":  (512, 768),
    "9:16": (432, 768),
}


def figure(prompt: str, caption: str = "", alt: str = "",
           aspect: str = "16:9") -> str:
    """<Figure> — a custom AI-generated illustration.

    `prompt`  — plain-language description of the image to generate.
    `caption` — caption shown beneath the figure (defaults to none).
    `alt`     — accessibility text (defaults to the prompt).
    `aspect`  — one of 16:9 | 3:2 | 4:3 | 1:1 | 2:3 | 9:16 (default 16:9).
    """
    prompt = (prompt or "").strip()
    width, height = _ASPECTS.get(aspect, _ASPECTS["16:9"])
    alt_text = (alt or prompt or "generated illustration").strip()

    cap_html = (
        f'<figcaption class="b-figure-cap">{_esc(caption)}</figcaption>'
        if caption else ""
    )

    if not prompt:
        return (
            '<figure class="b-figure b-figure-placeholder">'
            '<div class="b-figure-frame"><div class="b-figure-note">'
            'figure — no prompt given</div></div>'
            f'{cap_html}</figure>'
        )

    # Try a real generation when enabled. Import lazily so the render path has
    # no hard dependency on the imagegen module unless a figure is used.
    data_uri = None
    try:
        from ...imagegen import figure_data_uri, image_generation_enabled
        if image_generation_enabled():
            data_uri = figure_data_uri(prompt, width=width, height=height)
    except Exception:  # noqa: BLE001 — never let a figure break the packet
        data_uri = None

    if data_uri:
        return (
            f'<figure class="b-figure" style="--fig-aspect:{width}/{height}">'
            f'<img class="b-figure-img" src="{data_uri}" alt="{_esc(alt_text, quote=True)}" '
            f'loading="lazy" width="{width}" height="{height}">'
            f'{cap_html}</figure>'
        )

    # Disabled, or generation failed — a clean placeholder. The prompt is
    # shown so the reader (and the author) know what was intended.
    return (
        f'<figure class="b-figure b-figure-placeholder" '
        f'style="--fig-aspect:{width}/{height}">'
        '<div class="b-figure-frame">'
        '<svg class="b-figure-icon" viewBox="0 0 24 24" fill="none" '
        'aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2" '
        'stroke="currentColor" stroke-width="1.6"/><circle cx="8.5" cy="9.5" '
        'r="1.8" stroke="currentColor" stroke-width="1.6"/><path d="M4 17l5-5 '
        '4 4 3-3 4 4" stroke="currentColor" stroke-width="1.6" '
        'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        f'<div class="b-figure-note">Illustration: {_esc(prompt)}</div>'
        '</div>'
        f'{cap_html}</figure>'
    )
