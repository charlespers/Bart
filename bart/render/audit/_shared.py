"""Shared primitives for the format-audit package.

The audit splits into three concerns — ``checks`` (read-only detection),
``fixes`` (idempotent autofixes), ``rebuild`` (the 3-stage driver + the
streaming-corruption rebuild). This module holds what they have in common:

  * the ``AuditIssue`` / ``AuditResult`` data model;
  * the "protected region" helpers — both checks and fixes must skip
    ``<pre>`` / ``<code>`` / ``<script>`` / ``<style>`` spans when
    pattern-matching backslashes / brackets / angle brackets (touching those
    outside the tag boundary was the source of the infamous "format --fix
    re-introduces the rendering bug it just fixed" regression);
  * the regexes that a check and its paired fix both reference.

Internal to ``bart.render.audit``; import the public surface from
``bart.render.audit`` (or the back-compat ``bart.render.format_audit`` shim).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


# ─── Issue model ─────────────────────────────────────────────────


@dataclass
class AuditIssue:
    file: str          # path relative to run_dir
    kind: str          # short identifier — used to dedupe + group
    detail: str        # human-readable, one line
    severity: str = "warn"   # "info" | "warn" | "error"
    line: int | None = None  # best-effort line number where applicable
    fix_applied: bool = False


@dataclass
class AuditResult:
    files_scanned: int = 0
    issues: list[AuditIssue] = field(default_factory=list)
    fixes_applied: int = 0

    @property
    def by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self.issues:
            out[i.severity] = out.get(i.severity, 0) + 1
        return out

    @property
    def errors(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[AuditIssue]:
        return [i for i in self.issues if i.severity == "warn"]


def _line_of(text: str, offset: int) -> int:
    """Best-effort 1-indexed line number for a string offset."""
    return text.count("\n", 0, offset) + 1


def _strip_protected(html: str) -> str:
    """Remove `<pre>`, `<code>`, `<script>`, `<style>` content for checks
    that should ignore those regions (math balance, dollar leak, etc.)."""
    html = re.sub(r"<pre\b[^>]*>.*?</pre>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<code\b[^>]*>.*?</code>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style\b[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    return html


_PROTECTED_RE = re.compile(
    r"<(pre|code|script|style)\b[^>]*>.*?</\1>",
    flags=re.DOTALL | re.IGNORECASE,
)


def _apply_outside_protected(
    html: str, transform: Callable[[str], tuple[str, int]]
) -> tuple[str, int]:
    """Run `transform` on every span of HTML *outside* `<pre>`, `<code>`,
    `<script>`, `<style>` regions, and stitch the result back together.

    Critical for autofixes that pattern-match on backslashes / brackets /
    angle brackets — those characters are load-bearing inside `<script>`
    (KaTeX delimiter config, mhchem loader heuristics) and inside `<pre>`
    (literal source examples). Touching them outside-the-tag-boundary was
    the source of the infamous "format --fix re-introduces the rendering
    bug it just fixed" regression.

    Returns the rebuilt HTML and a count of fixes applied across all
    non-protected spans.
    """
    out: list[str] = []
    cursor = 0
    total = 0
    for m in _PROTECTED_RE.finditer(html):
        chunk = html[cursor : m.start()]
        new, n = transform(chunk)
        out.append(new)
        total += n
        out.append(m.group(0))  # protected region untouched
        cursor = m.end()
    if cursor < len(html):
        new, n = transform(html[cursor:])
        out.append(new)
        total += n
    return "".join(out), total


# Match `\\(`, `\\)`, `\\[`, `\\]` — but NOT `\\[6pt]` / `\\[1em]` / etc.,
# which is the LaTeX `\\` (linebreak) followed by an optional vertical-space
# argument `[Npt]`. Collapsing those would smash `\\[6pt]\text{1 order}` into
# `\[6pt]\text{1 order}`, and KaTeX then treats the `\[` as a brand-new
# display-math opener — splitting the equation onto multiple visual lines and
# leaving stray `\[6pt]` source in the page. (Used by `_check_double_escaped_math`
# and `_fix_double_escaped_math`.)
_DOUBLE_ESCAPED = re.compile(
    r"\\\\(\(|\)|\[(?!\d+\s*(?:pt|em|in|mm|cm|ex|sp|pc|bp|dd|cc)\b)|\])"
)


_DOUBLE_ENTITY = re.compile(r"&amp;(?:amp|lt|gt|quot|apos|nbsp|#\d+);")


_EMPTY_MATH_RE = re.compile(r"\\\(\s*\\\)|\\\[\s*\\\]")


_HTML_ENT_BACKSLASH = re.compile(r"&(?:#x?5[Cc]|#92|bsol);")


_HTML_ENT_AMP = re.compile(r"&amp;")


_HTML_ENT_LT = re.compile(r"&lt;")


_HTML_ENT_GT = re.compile(r"&gt;")


_IMG_TAG = re.compile(r"<img\b([^>]*)>", re.IGNORECASE)


_DISPLAY_DOLLAR_RE = re.compile(
    r"(?<!\\)\$\$([^\$\n][\s\S]*?[^\$\n])\$\$(?!\$)",
    re.MULTILINE,
)


_INLINE_DOLLAR_RE = re.compile(
    r"(?<![\\$])\$(?!\$)([^\$\n]+?)(?<![\\])\$(?!\$)"
)


_MATH_CONTENT_HINT = re.compile(
    r"\\[A-Za-z]+|[_^]\{|[_^][A-Za-z0-9]|\\frac|\\sqrt"
)


_JSON_UNICODE_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")


_MATH_SPAN_RE = re.compile(r"(\\\(.+?\\\)|\\\[.+?\\\])", re.DOTALL)


_MISMATCHED_OPEN_PAREN = re.compile(r"\\\(([\s\S]*?)\\\]")


_MISMATCHED_OPEN_BRACKET = re.compile(r"\\\[([\s\S]*?)\\\)")


_TEX_MATH_INDICATOR_CMDS = (
    # Operators / arithmetic
    r"int|iint|iiint|oint|sum|prod|coprod|frac|dfrac|tfrac|binom|dbinom|tbinom|"
    r"sqrt|cbrt|infty|partial|nabla|cdot|cdots|ldots|vdots|ddots|dots|"
    r"times|div|pm|mp|ast|star|circ|bullet|"
    r"oplus|otimes|odot|ominus|oslash|wedge|vee|cap|cup|"
    # Relations / comparison
    r"leq|geq|le|ge|ll|gg|neq|ne|approx|equiv|sim|simeq|cong|asymp|propto|"
    r"implies|impliedby|iff|leftrightarrow|"
    r"prec|succ|preceq|succeq|"
    # Arrows
    r"to|gets|rightarrow|leftarrow|Rightarrow|Leftarrow|Leftrightarrow|"
    r"longrightarrow|longleftarrow|longleftrightarrow|"
    r"Longrightarrow|Longleftarrow|Longleftrightarrow|"
    r"xrightarrow|xleftarrow|xleftrightarrow|mapsto|hookrightarrow|hookleftarrow|"
    # Greek lowercase
    r"alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|vartheta|"
    r"iota|kappa|varkappa|lambda|mu|nu|xi|omicron|pi|varpi|rho|varrho|sigma|"
    r"varsigma|tau|upsilon|phi|varphi|chi|psi|omega|digamma|"
    # Greek uppercase
    r"Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega|"
    # Accents / decorations
    r"vec|overrightarrow|overleftarrow|hat|widehat|bar|overline|underline|"
    r"tilde|widetilde|dot|ddot|dddot|breve|grave|acute|check|mathring|"
    r"overbrace|underbrace|overset|underset|stackrel|"
    # Functions
    r"sin|cos|tan|cot|sec|csc|arcsin|arccos|arctan|arccot|sinh|cosh|tanh|coth|"
    r"log|ln|lg|exp|lim|liminf|limsup|sup|inf|max|min|arg|det|deg|gcd|lcm|"
    r"ker|dim|hom|Pr|"
    # Math fonts / styles
    r"mathbb|mathbf|mathcal|mathfrak|mathrm|mathit|mathsf|mathtt|mathnormal|"
    r"boldsymbol|bm|"
    r"text|textit|textbf|textrm|textsf|texttt|textnormal|"
    r"displaystyle|textstyle|scriptstyle|scriptscriptstyle|"
    # Sizing / framing
    r"left|right|big|Big|bigg|Bigg|biggl|biggr|bigl|bigr|"
    r"boxed|framebox|fbox|underbrace|overbrace|"
    # Environments
    r"begin|end|"
    # Logic / sets
    r"forall|exists|nexists|in|notin|ni|subset|supset|subseteq|supseteq|"
    r"setminus|emptyset|varnothing|complement|"
    # Delimiters
    r"langle|rangle|lceil|rceil|lfloor|rfloor|lvert|rvert|lVert|rVert|"
    r"vert|Vert|backslash|"
    # Special symbols
    r"aleph|beth|hbar|ell|Re|Im|imath|jmath|wp|"
    r"prime|dagger|ddagger|S|P|copyright|pounds|"
    r"square|blacksquare|triangle|triangleleft|triangleright|"
    r"diamond|lozenge|spadesuit|heartsuit|diamondsuit|clubsuit|"
    r"angle|measuredangle|sphericalangle|degree|"
    # Modular / number theory
    r"mod|bmod|pmod|gcd|lcm|"
    # Spaces (worded)
    r"quad|qquad|space|thinspace|medspace|thickspace|negthinspace|negmedspace|"
    r"negthickspace|"
    # Linear algebra / probability
    r"det|tr|rank|"
    # Misc binary / relations
    r"mid|nmid|parallel|nparallel|perp|bot|top|"
    r"vdash|dashv|models|"
    # Chemistry (mhchem) — `\ce{...}` `\pu{...}`
    r"ce|pu|"
    # Quantum / physics
    r"bra|ket|braket|"
    # Cases / matrices (already have `begin/end` but bare tokens leak too)
    r"matrix|pmatrix|bmatrix|vmatrix|Vmatrix|smallmatrix|cases|aligned|gathered|"
    # Legacy
    r"over|atop|choose|root"
)


_MATH_BODY_CHAR = "A-Za-z0-9()_^{}\\[\\]+\\-*/=."


_RAW_TEX_CMD_RE = re.compile(
    rf"\\(?:{_TEX_MATH_INDICATOR_CMDS})(?![A-Za-z])|\\[,;:!]"
)

