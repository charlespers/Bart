"""Physics reference card. Static; zero LLM tokens.

Auto-fires for physics / mechanics / E&M / thermodynamics / optics subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "physics", "mechanics", "kinematic", "dynamics", "thermodynamic",
    "electromagnet", "electricity", "magnetism", "optics", "quantum",
    "relativity", "astrophys", "classical mechanics", "statics",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="Fundamental constants & SI units",
        tag="constants",
        kind="table",
        data={
            "headers": ["Quantity", "Symbol", "Value"],
            "rows": [
                ["Speed of light",        "c",      "2.998 × 10⁸ m/s"],
                ["Gravitational const.",  "G",      "6.674 × 10⁻¹¹ N·m²/kg²"],
                ["Free-fall accel. (Earth)", "g",   "9.81 m/s²"],
                ["Planck constant",       "h",      "6.626 × 10⁻³⁴ J·s"],
                ["Elementary charge",     "e",      "1.602 × 10⁻¹⁹ C"],
                ["Electron mass",         "mₑ",     "9.109 × 10⁻³¹ kg"],
                ["Boltzmann constant",    "k_B",    "1.381 × 10⁻²³ J/K"],
                ["Avogadro number",       "N_A",    "6.022 × 10²³ /mol"],
                ["Coulomb constant",      "k",      "8.988 × 10⁹ N·m²/C²"],
                ["Permittivity of vacuum","ε₀",     "8.854 × 10⁻¹² F/m"],
            ],
        },
    ),
    RefSection(
        title="Kinematics & mechanics",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Constant-acceleration set",
             "formula": r"v = v_0 + at,\quad x = x_0 + v_0 t + \tfrac12 a t^2,\quad v^2 = v_0^2 + 2a\,\Delta x"},
            {"name": "Newton's second law",
             "formula": r"\vec F_{\text{net}} = m\vec a",
             "note": "the sum of all forces, not any single one"},
            {"name": "Work–energy theorem",
             "formula": r"W_{\text{net}} = \Delta KE = \tfrac12 m v^2 - \tfrac12 m v_0^2"},
            {"name": "Momentum & impulse",
             "formula": r"\vec p = m\vec v,\qquad \vec J = \Delta \vec p = \vec F\,\Delta t"},
            {"name": "Gravitational & elastic PE",
             "formula": r"U_g = mgh,\qquad U_s = \tfrac12 k x^2"},
            {"name": "Circular motion",
             "formula": r"a_c = \frac{v^2}{r},\qquad F_c = \frac{m v^2}{r}"},
            {"name": "Simple harmonic motion",
             "formula": r"T = 2\pi\sqrt{m/k},\qquad \omega = \sqrt{k/m}"},
        ],
    ),
    RefSection(
        title="Electricity & magnetism",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Coulomb's law",
             "formula": r"F = k\frac{q_1 q_2}{r^2}"},
            {"name": "Electric field & potential",
             "formula": r"E = \frac{F}{q},\qquad V = \frac{U}{q},\qquad E = -\frac{dV}{dx}"},
            {"name": "Ohm's law & power",
             "formula": r"V = IR,\qquad P = IV = I^2 R = \frac{V^2}{R}"},
            {"name": "Capacitance",
             "formula": r"C = \frac{Q}{V},\qquad U_C = \tfrac12 C V^2"},
            {"name": "Magnetic force",
             "formula": r"\vec F = q\vec v \times \vec B,\qquad F = BIL\sin\theta"},
            {"name": "Faraday's law",
             "formula": r"\mathcal{E} = -\frac{d\Phi_B}{dt}"},
        ],
    ),
    RefSection(
        title="Thermodynamics, waves & optics",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Ideal gas law",
             "formula": r"PV = nRT = N k_B T"},
            {"name": "First law of thermodynamics",
             "formula": r"\Delta U = Q - W",
             "note": "Q in, W done by the system"},
            {"name": "Wave relation",
             "formula": r"v = f\lambda,\qquad T = 1/f"},
            {"name": "Snell's law",
             "formula": r"n_1 \sin\theta_1 = n_2 \sin\theta_2"},
            {"name": "Thin lens / mirror",
             "formula": r"\frac{1}{f} = \frac{1}{d_o} + \frac{1}{d_i},\qquad m = -\frac{d_i}{d_o}"},
            {"name": "Photon energy",
             "formula": r"E = hf = \frac{hc}{\lambda}"},
        ],
    ),
    RefSection(
        title="Problem-solving checklist",
        tag="method",
        kind="list",
        data=[
            {"term": "Draw it", "explain": "Free-body diagram first; label every force and axis before any algebra."},
            {"term": "Pick a frame", "explain": "Choose axes that make the most forces lie along an axis."},
            {"term": "Conserve what's conserved", "explain": "Closed system → momentum conserved; no friction/non-conservative work → mechanical energy conserved."},
            {"term": "Check units", "explain": "Every term in an equation must share units; a mismatch means an error upstream."},
            {"term": "Sanity-check limits", "explain": "Let a variable → 0 or ∞ and confirm the answer behaves sensibly."},
        ],
    ),
]


class PhysicsReferenceTool:
    name = "physics-reference"
    description = "Static physics reference card (constants, mechanics, E&M, thermo)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_PHYSICS", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_PHYSICS=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match physics keywords")
        path = write_ref_page(packet_dir, "physics", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
