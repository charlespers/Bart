"""Statistics & probability reference card. Static; zero LLM tokens.

Auto-fires for statistics / probability / data-analysis subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "statistic", "probability", "biostat", "data analysis", "regression",
    "inference", "stochastic", "bayesian", "experimental design",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="Descriptive statistics",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Sample mean",
             "formula": r"\bar x = \frac{1}{n}\sum_{i=1}^n x_i"},
            {"name": "Sample variance / SD",
             "formula": r"s^2 = \frac{1}{n-1}\sum (x_i - \bar x)^2,\qquad s = \sqrt{s^2}",
             "note": "n − 1 (Bessel's correction) for an unbiased estimate"},
            {"name": "Standard error of the mean",
             "formula": r"\text{SE} = \frac{s}{\sqrt n}"},
            {"name": "z-score",
             "formula": r"z = \frac{x - \mu}{\sigma}"},
            {"name": "Correlation coefficient",
             "formula": r"r = \frac{\sum (x_i-\bar x)(y_i-\bar y)}{(n-1)\,s_x s_y}"},
        ],
    ),
    RefSection(
        title="Probability rules",
        tag="probability",
        kind="formulas",
        data=[
            {"name": "Addition rule",
             "formula": r"P(A \cup B) = P(A) + P(B) - P(A \cap B)"},
            {"name": "Conditional probability",
             "formula": r"P(A \mid B) = \frac{P(A \cap B)}{P(B)}"},
            {"name": "Bayes' theorem",
             "formula": r"P(A \mid B) = \frac{P(B \mid A)\,P(A)}{P(B)}"},
            {"name": "Expected value & variance",
             "formula": r"E[X] = \sum x_i p_i,\qquad \operatorname{Var}(X) = E[X^2] - (E[X])^2"},
        ],
    ),
    RefSection(
        title="Common distributions",
        tag="distributions",
        kind="table",
        data={
            "headers": ["Distribution", "Use", "Mean", "Variance"],
            "rows": [
                ["Bernoulli(p)",  "single success/fail",      "p",      "p(1−p)"],
                ["Binomial(n,p)", "k successes in n trials",   "np",     "np(1−p)"],
                ["Poisson(λ)",    "rare events in an interval","λ",      "λ"],
                ["Geometric(p)",  "trials until first success","1/p",    "(1−p)/p²"],
                ["Uniform(a,b)",  "equally likely on [a,b]",   "(a+b)/2","(b−a)²/12"],
                ["Normal(μ,σ²)",  "sums/means (CLT)",          "μ",      "σ²"],
                ["Exponential(λ)","waiting time",              "1/λ",    "1/λ²"],
            ],
        },
    ),
    RefSection(
        title="Hypothesis testing",
        tag="inference",
        kind="list",
        data=[
            {"term": "Null vs. alternative", "explain": "H₀ = no effect / status quo; H₁ = the claim you test for."},
            {"term": "p-value", "explain": "P(data this extreme | H₀ true). Reject H₀ when p < α (commonly α = 0.05)."},
            {"term": "Type I error", "explain": "Reject a true H₀ — a false positive. Rate = α."},
            {"term": "Type II error", "explain": "Fail to reject a false H₀ — a false negative. Rate = β; power = 1 − β."},
            {"term": "Confidence interval", "explain": "estimate ± (critical value) × SE; a 95% CI captures the parameter in 95% of repeated samples."},
            {"term": "Test choice", "explain": "z-test (σ known, large n), t-test (σ unknown), χ² (categorical), ANOVA (≥3 group means)."},
        ],
    ),
    RefSection(
        title="Critical values (two-tailed)",
        tag="values",
        kind="table",
        data={
            "headers": ["Confidence", "α", "z critical"],
            "rows": [
                ["90%", "0.10", "1.645"],
                ["95%", "0.05", "1.960"],
                ["99%", "0.01", "2.576"],
            ],
        },
    ),
]


class StatisticsReferenceTool:
    name = "statistics-reference"
    description = "Static statistics reference card (descriptive, probability, distributions, testing)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_STATISTICS", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_STATISTICS=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match statistics keywords")
        path = write_ref_page(packet_dir, "statistics", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
