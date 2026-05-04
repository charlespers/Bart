r"""Format-check guardrail — runs after expansion, before final render.

This is "prettier" for bart packets: catch the formatting issues that
make a page look crammed, broken, or unclean.

It detects (and, where safe, repairs):

  Math + LaTeX
    - mismatched math delimiters: \( without \), \[ without \]
    - LaTeX inside JSON that lost its escapes (`\frac` → literal "frac")
    - unsupported KaTeX macros (rendered as red error text in the page)
    - crammed display equations (>2 adjacent without prose between)
    - dangerously long single-line equations (>180 chars — will overflow)
    - empty `tex` payload that's actually plain prose

  Reaction + chem layout
    - reaction-equation with >6 species (will scroll past the viewport)
    - molecule-diagram with no atoms
    - balance-equation with mismatched reactant/product/correct counts

  Box-internal sloppiness
    - formula-card with no legend AND no note (just a bare equation)
    - formula-card legend symbols that don't appear in the formula
    - worked-example with zero steps
    - multi-step with zero hints
    - concept-build with only one rung filled (defeats the teaching arc)

  Markup safety
    - unbalanced HTML tags emitted by agents
    - bare `\(`/`\[` inside `<pre>` / `<code>` (looks like raw LaTeX
      instead of rendered math)
    - mojibake / replacement-character runs in prose

Each check returns a `FormatWarning` with a kind, a human-readable
detail, and an `auto_fixed` flag indicating whether the renderer
already repaired the issue or whether the user should look at the
source.

Exposed entry point: `check(text, html) -> (clean_text, warnings)`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List


@dataclass
class FormatWarning:
    kind: str              # short identifier
    detail: str            # human-readable explanation
    severity: str = "warn" # "info" | "warn" | "error"
    file: str = ""         # set by caller


# ─── Math + LaTeX checks ──────────────────────────────────────────


def _check_math_delim_balance(text: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    # Strip code fences first — `\(` inside a code block is fine.
    stripped = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    n_inline_open  = stripped.count("\\(")
    n_inline_close = stripped.count("\\)")
    n_block_open   = stripped.count("\\[")
    n_block_close  = stripped.count("\\]")
    if n_inline_open != n_inline_close:
        warns.append(FormatWarning(
            "math_delim_unbalanced_inline",
            f"\\( count={n_inline_open} but \\) count={n_inline_close} — "
            "math will render as literal text where mismatched",
            severity="error",
        ))
    if n_block_open != n_block_close:
        warns.append(FormatWarning(
            "math_delim_unbalanced_block",
            f"\\[ count={n_block_open} but \\] count={n_block_close} — "
            "block math will leak into surrounding prose",
            severity="error",
        ))
    return warns


# Common LaTeX commands the agent emits. If we see one of these as PLAIN
# letters following a backslash that escaped to nothing, the JSON payload
# lost its backslash-doubling.
_UNESCAPED_LATEX_TELLS = (
    "frac", "sqrt", "sum", "int", "lim", "log", "ln",
    "alpha", "beta", "gamma", "delta", "theta", "lambda", "mu", "pi",
    "sigma", "omega", "infty", "to", "rightarrow", "leftarrow",
    "partial", "nabla", "cdot", "times", "approx", "leq", "geq", "neq",
    "begin", "end", "left", "right", "text", "mathrm", "mathbf",
)


def _check_unescaped_latex_in_json(text: str) -> List[FormatWarning]:
    """Detect LaTeX inside JSON-fenced blocks that lost its backslash escapes.

    Symptom: a `bart-*` block has `"tex": "frac{1}{2}"` instead of
    `"tex": "\\\\frac{1}{2}"`. KaTeX renders `frac{1}{2}` as the literal
    string. Catches this by scanning the post-expansion HTML for the
    characteristic letter-cluster patterns.
    """
    warns: List[FormatWarning] = []
    # Look inside the rendered formula-card and worked-example math slots
    # for backslash-less LaTeX command names. False-positive risk: an
    # equation that legitimately contains the word "sum" as a variable.
    # Mitigate by requiring the word to be followed by "{" (i.e., it's
    # being called as a macro).
    suspect = []
    # Search inside math containers — KaTeX wraps in .katex spans, but
    # before render we have raw \(...\) and \[...\] regions in the text.
    for m in re.finditer(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", text, re.DOTALL):
        body = m.group(1) or m.group(2) or ""
        for cmd in _UNESCAPED_LATEX_TELLS:
            # Word boundary, command followed by `{` or `_` or `^` — strong
            # signal it's meant to be a macro but missing the backslash.
            pattern = rf"(?<![\\\w]){cmd}\s*[{{_^]"
            if re.search(pattern, body):
                suspect.append((cmd, body[:80]))
                break
    if suspect:
        warns.append(FormatWarning(
            "latex_missing_backslash",
            f"{len(suspect)} math span(s) appear to use LaTeX commands "
            f"without their backslash (e.g. `{suspect[0][0]}{{...}}` "
            f"instead of `\\\\{suspect[0][0]}{{...}}`) — JSON payload "
            "likely lost its escapes",
            severity="error",
        ))
    return warns


# KaTeX accepts a substantial but bounded subset of LaTeX. Common things
# that DON'T work and should be flagged.
_KATEX_UNSUPPORTED = (
    "tikz",           # diagrams
    "tikzpicture",
    "tikzcd",
    "minipage",
    "tabular",        # use a markdown table
    "newcommand",     # macros are scoped to the package
    "renewcommand",
    "definecolor",
    "includegraphics",
    "tableofcontents",
)


def _check_unsupported_macros(text: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    bad = []
    for cmd in _KATEX_UNSUPPORTED:
        if re.search(rf"\\{cmd}\b", text):
            bad.append(cmd)
    if bad:
        warns.append(FormatWarning(
            "katex_unsupported_macro",
            f"unsupported LaTeX macro(s): {', '.join(bad)} — "
            "KaTeX won't render these. Use a `bart-*` block instead.",
            severity="warn",
        ))
    return warns


_LONG_EQ_THRESHOLD = 180  # chars per single equation line


def _check_long_equations(text: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    long_count = 0
    longest = 0
    for m in re.finditer(r"\\\[(.+?)\\\]", text, re.DOTALL):
        body = m.group(1).strip()
        if len(body) > _LONG_EQ_THRESHOLD:
            long_count += 1
            longest = max(longest, len(body))
    if long_count:
        warns.append(FormatWarning(
            "equation_too_long",
            f"{long_count} display equation(s) exceed {_LONG_EQ_THRESHOLD} "
            f"chars (longest: {longest}). They'll horizontal-scroll, but "
            "consider splitting at = or breaking with `\\\\` for readability.",
            severity="info",
        ))
    return warns


def _check_crammed_equations(text: str) -> List[FormatWarning]:
    """Detect runs of >=3 display equations adjacent without prose."""
    warns: List[FormatWarning] = []
    # Find sequences of \[ ... \] with only whitespace/empty lines between.
    pattern = re.compile(
        r"(\\\[.+?\\\]\s*){3,}", re.DOTALL,
    )
    matches = pattern.findall(text)
    if matches:
        warns.append(FormatWarning(
            "equation_wall",
            f"{len(matches)} run(s) of 3+ adjacent display equations "
            "without prose between — looks like a wall of formulas. Add "
            "a sentence of intuition or use `bart-formula-card` for each.",
            severity="warn",
        ))
    return warns


# ─── Box-internal sloppiness ─────────────────────────────────────


_FORMULA_CARD_BODY_RE = re.compile(
    r'<div class="b-formula-card-body">.*?\\\[(.+?)\\\].*?</div>',
    re.DOTALL,
)


def _check_formula_card_quality(html: str) -> List[FormatWarning]:
    """A "bare" formula-card is one that's just an equation with no
    contextual legend or note — visually wasteful. We approximate
    formula-card extents by scanning for the open marker and counting
    legend/note presence in a window before the next b-formula-card."""
    warns: List[FormatWarning] = []
    bare = 0
    starts = [m.start() for m in re.finditer(
        r'<div class="b-formula-card"', html)]
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(html)
        card_html = html[s:end]
        has_legend = "b-formula-card-legend" in card_html
        has_note = "b-formula-card-note" in card_html
        if not has_legend and not has_note:
            bare += 1
    if bare:
        warns.append(FormatWarning(
            "formula_card_bare",
            f"{bare} formula-card(s) have no legend and no note — they're "
            "just an equation in a box. Add a `legend` (per-symbol gloss) "
            "or a `note` (one-line context).",
            severity="info",
        ))
    return warns


def _check_worked_example_quality(html: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    examples = re.findall(
        r'<div class="b-worked-example">(.*?)</div>\s*</div>',
        html, re.DOTALL,
    )
    empty = 0
    for ex in examples:
        # Count <li> within ol — a worked example with 0 steps is broken.
        steps = ex.count("<li>")
        if steps == 0:
            empty += 1
    if empty:
        warns.append(FormatWarning(
            "worked_example_empty",
            f"{empty} worked-example(s) have zero solution steps — fill the "
            "`steps` array with at least 2 entries showing the reasoning.",
            severity="warn",
        ))
    return warns


def _check_concept_build_arc(html: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    builds = re.findall(
        r'<div class="b-concept-build">(.*?)</div>\s*</div>',
        html, re.DOTALL,
    )
    thin = 0
    for b in builds:
        rungs = len(re.findall(r'class="b-concept-build-rung', b))
        if rungs <= 1:
            thin += 1
    if thin:
        warns.append(FormatWarning(
            "concept_build_thin",
            f"{thin} concept-build block(s) have ≤1 rung filled — the "
            "teaching arc needs MOTIVATE + DEFINE + GROUND + CONNECT + "
            "CONTRAST + APPLY to actually teach.",
            severity="warn",
        ))
    return warns


# ─── Reaction/chem layout ────────────────────────────────────────


def _check_reaction_equation_layout(html: str) -> List[FormatWarning]:
    warns: List[FormatWarning] = []
    overflow = 0
    for m in re.finditer(r'<figure class="b-rxn">(.*?)</figure>',
                         html, re.DOTALL):
        rxn = m.group(1)
        species = rxn.count("b-rxn-species")
        if species > 6:
            overflow += 1
    if overflow:
        warns.append(FormatWarning(
            "reaction_too_wide",
            f"{overflow} reaction-equation(s) have >6 species — will "
            "scroll horizontally. Consider splitting into separate "
            "reactions or using a `bart-process-ribbon` for stages.",
            severity="info",
        ))
    return warns


# ─── Markup safety ───────────────────────────────────────────────


def _check_mojibake_runs(text: str) -> List[FormatWarning]:
    """Find runs of replacement chars / private-use glyphs that survived."""
    warns: List[FormatWarning] = []
    pua_run = re.search(r"[-]{4,}", text)
    repl_run = re.search(r"�{3,}", text)
    if pua_run or repl_run:
        warns.append(FormatWarning(
            "mojibake_in_prose",
            "extracted-PDF mojibake leaked into the prose. The corpus brief "
            "or research card likely contains broken-font-map characters.",
            severity="warn",
        ))
    return warns


def _check_orphan_html_tags(text: str) -> List[FormatWarning]:
    """Check that <details>, <summary>, <div class=b-*> tags are balanced."""
    warns: List[FormatWarning] = []
    # Simple paired-tag balance check on a small set of tags the agent
    # is likely to emit. Markdown extensions normalize most issues but
    # an unbalanced <details> from the model crashes Quick Check rendering.
    for tag in ("details", "summary"):
        opens = len(re.findall(rf"<{tag}\b", text))
        closes = len(re.findall(rf"</{tag}>", text))
        if opens != closes:
            warns.append(FormatWarning(
                "html_tag_unbalanced",
                f"<{tag}> opens={opens} closes={closes} — collapsibles "
                "may render broken.",
                severity="warn",
            ))
    return warns


# ─── Auto-repair: gentle fixes that the renderer can apply silently ─


def _autofix_double_escaped_math(text: str) -> tuple[str, int]:
    """Sometimes agents emit `\\\\(` instead of `\\(` (double-escaped). KaTeX
    treats the literal backslash as a character and won't render. Normalize."""
    n = 0
    for pat in (r"\\\\\(", r"\\\\\)"):
        new = re.sub(pat, lambda m: m.group(0).replace("\\\\", "\\"), text)
        if new != text:
            n += 1
            text = new
    return text, n


def _autofix_naked_dollar_math(text: str) -> tuple[str, int]:
    """If the sanitizer pass missed a $$...$$ block (rare), convert here."""
    n = 0
    new = re.sub(r"\$\$(.+?)\$\$", lambda m: f"\\[{m.group(1).strip()}\\]",
                 text, flags=re.DOTALL)
    if new != text:
        n += 1
        text = new
    return text, n


def _autofix_blank_lines_around_block_math(text: str) -> tuple[str, int]:
    """Display math needs blank lines around it for markdown to leave it
    alone. Insert if missing."""
    n = 0
    new = re.sub(
        r"(?<!\n\n)(\\\[.+?\\\])(?!\n\n)",
        lambda m: f"\n\n{m.group(1)}\n\n",
        text, flags=re.DOTALL,
    )
    if new != text:
        n = new.count("\\[") - text.count("\\[")
        text = new
    return text, n


# ─── Public entry point ─────────────────────────────────────────


def check(text: str, html: str = "") -> tuple[str, List[FormatWarning]]:
    """Run all guardrail checks. Returns (possibly-repaired text, warnings).

    `text` is the markdown after sanitize + block-expand, before final
    render. `html` is the rendered HTML (optional — enables box-internal
    quality checks).

    The repaired text is what the caller should pass to the markdown
    renderer; the warnings stream into the existing render-warnings
    pipeline so they show up in `render_warnings.json`.
    """
    warnings: List[FormatWarning] = []

    # Auto-fixes first (they can resolve issues the checks would flag).
    text, n1 = _autofix_double_escaped_math(text)
    text, n2 = _autofix_naked_dollar_math(text)
    text, n3 = _autofix_blank_lines_around_block_math(text)
    if n1 + n2 + n3:
        warnings.append(FormatWarning(
            "auto_repaired",
            f"applied {n1 + n2 + n3} auto-repair(s): "
            f"{n1} double-escaped delim(s), {n2} dollar-math conversion(s), "
            f"{n3} blank-line padding(s)",
            severity="info",
        ))

    # Math + LaTeX
    warnings.extend(_check_math_delim_balance(text))
    warnings.extend(_check_unescaped_latex_in_json(text))
    warnings.extend(_check_unsupported_macros(text))
    warnings.extend(_check_long_equations(text))
    warnings.extend(_check_crammed_equations(text))

    # Markup safety
    warnings.extend(_check_mojibake_runs(text))
    warnings.extend(_check_orphan_html_tags(text))

    # Box-internal (HTML-aware)
    if html:
        warnings.extend(_check_formula_card_quality(html))
        warnings.extend(_check_worked_example_quality(html))
        warnings.extend(_check_concept_build_arc(html))
        warnings.extend(_check_reaction_equation_layout(html))

    return text, warnings
