"""ECE / Signals & Systems / Circuits reference card. Static; zero LLM tokens.

Auto-fires for ECE / signals / systems / control / DSP / circuits subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "ece", "electrical", "signal", "system", "circuit", "dsp",
    "fourier", "laplace", "z-transform", "control", "communications",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="Standard signals",
        tag="signals",
        kind="table",
        data={
            "headers": ["Signal", "Time domain", "Property"],
            "rows": [
                ["Unit impulse",   {"math": r"\delta(t)"},                "integrates to 1; sifting property"],
                ["Unit step",      {"math": r"u(t)"},                     "0 for t<0, 1 for t≥0"],
                ["Ramp",           {"math": r"r(t) = t\,u(t)"},           "integral of u(t)"],
                ["Rectangular",    {"math": r"\mathrm{rect}(t/T)"},       "1 for |t| < T/2"],
                ["Sinc",           {"math": r"\mathrm{sinc}(t) = \sin(\pi t)/(\pi t)"}, "FT of rect; ideal LPF impulse response"],
                ["Exponential",    {"math": r"e^{at}u(t)"},               "stable for Re(a) < 0"],
                ["Sinusoid",       {"math": r"\cos(\omega_0 t + \phi)"},  "FT = pair of impulses at ±ω₀"],
            ],
        },
    ),
    RefSection(
        title="Fourier transform pairs",
        tag="FT",
        kind="table",
        data={
            "headers": ["x(t)", "X(jω)"],
            "rows": [
                [{"math": r"\delta(t)"},               {"math": "1"}],
                [{"math": "1"},                        {"math": r"2\pi\,\delta(\omega)"}],
                [{"math": r"u(t)"},                    {"math": r"\pi\,\delta(\omega) + \tfrac{1}{j\omega}"}],
                [{"math": r"e^{-at}u(t),\;a>0"},       {"math": r"\frac{1}{a + j\omega}"}],
                [{"math": r"e^{-a|t|},\;a>0"},         {"math": r"\frac{2a}{a^2 + \omega^2}"}],
                [{"math": r"\mathrm{rect}(t/T)"},      {"math": r"T\,\mathrm{sinc}(\omega T / 2\pi)"}],
                [{"math": r"\cos(\omega_0 t)"},        {"math": r"\pi[\delta(\omega-\omega_0) + \delta(\omega+\omega_0)]"}],
                [{"math": r"\sin(\omega_0 t)"},        {"math": r"j\pi[\delta(\omega+\omega_0) - \delta(\omega-\omega_0)]"}],
            ],
        },
    ),
    RefSection(
        title="Laplace transform pairs (causal)",
        tag="L{·}",
        kind="table",
        data={
            "headers": ["x(t), t≥0", "X(s)", "ROC"],
            "rows": [
                [{"math": r"\delta(t)"},           {"math": "1"},                                "all s"],
                [{"math": "1"},                    {"math": r"\frac{1}{s}"},                     "Re(s) > 0"],
                [{"math": r"e^{-at}"},             {"math": r"\frac{1}{s+a}"},                   "Re(s) > -a"],
                [{"math": "t"},                    {"math": r"\frac{1}{s^2}"},                   "Re(s) > 0"],
                [{"math": r"t^n"},                 {"math": r"\frac{n!}{s^{n+1}}"},              "Re(s) > 0"],
                [{"math": r"\sin(\omega_0 t)"},    {"math": r"\frac{\omega_0}{s^2 + \omega_0^2}"}, "Re(s) > 0"],
                [{"math": r"\cos(\omega_0 t)"},    {"math": r"\frac{s}{s^2 + \omega_0^2}"},       "Re(s) > 0"],
                [{"math": r"e^{-at}\sin(\omega_0 t)"}, {"math": r"\frac{\omega_0}{(s+a)^2 + \omega_0^2}"}, "Re(s) > -a"],
            ],
        },
    ),
    RefSection(
        title="Core LTI relations",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Convolution",
             "formula": r"y(t) = (x * h)(t) = \int_{-\infty}^{\infty} x(\tau)\,h(t-\tau)\,d\tau",
             "note": "convolution in t ↔ multiplication in frequency"},
            {"name": "Frequency response",
             "formula": r"Y(j\omega) = H(j\omega)\,X(j\omega)"},
            {"name": "Magnitude in dB",
             "formula": r"|H|_\mathrm{dB} = 20 \log_{10} |H(j\omega)|"},
            {"name": "Sampling theorem",
             "formula": r"f_s > 2 f_\max",
             "note": "Nyquist: sample faster than twice the highest frequency"},
            {"name": "DFT",
             "formula": r"X[k] = \sum_{n=0}^{N-1} x[n]\,e^{-j 2\pi k n / N}"},
            {"name": "Z-transform",
             "formula": r"X(z) = \sum_{n=-\infty}^{\infty} x[n]\,z^{-n}"},
        ],
    ),
    RefSection(
        title="Bode plot rules-of-thumb",
        tag="bode",
        kind="list",
        data=[
            {"term": "Pole at p", "explain": "−20 dB/decade above |p|; −45° phase at p, −90° asymptote."},
            {"term": "Zero at z", "explain": "+20 dB/decade above |z|; +45° phase at z, +90° asymptote."},
            {"term": "Gain K",    "explain": "constant 20·log₁₀|K| dB; 0° phase if K>0, 180° if K<0."},
            {"term": "Pole at origin", "explain": "−20 dB/decade across all frequencies; constant −90° phase."},
            {"term": "Complex pair", "explain": "−40 dB/decade above ω_n; phase swings 180° around ω_n; peaking ~ 1/(2ζ)."},
        ],
    ),
    RefSection(
        title="Circuits — element relations",
        tag="circuits",
        kind="formulas",
        data=[
            {"name": "Resistor",  "formula": r"v = i R"},
            {"name": "Inductor",  "formula": r"v = L \frac{di}{dt}",   "note": r"impedance jωL"},
            {"name": "Capacitor", "formula": r"i = C \frac{dv}{dt}",   "note": r"impedance 1/(jωC)"},
            {"name": "Op-amp (ideal, neg fb)",
             "formula": r"v_+ = v_-,\quad i_+ = i_- = 0"},
            {"name": "Inverting amp gain",
             "formula": r"A = -\frac{R_f}{R_\mathrm{in}}"},
            {"name": "Non-inverting amp gain",
             "formula": r"A = 1 + \frac{R_f}{R_\mathrm{in}}"},
        ],
    ),
]


class ECEReferenceTool:
    name = "ece-reference"
    description = "Static ECE reference card (signals, FT/Laplace pairs, Bode, circuits)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_ECE", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_ECE=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match ECE keywords")
        path = write_ref_page(packet_dir, "ece", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
