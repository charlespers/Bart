"""Chemistry reference card. Static; zero LLM tokens.

Auto-fires when the subject contains a chem-related keyword. Users can
also force-include via env var BART_TOOL_CHEM=1 or skip with =0.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = ("chem", "organic", "inorganic", "biochem", "pchem", "thermodynamic", "kinetic")


SECTIONS: list[RefSection] = [
    RefSection(
        title="Common functional groups",
        tag="organic",
        kind="table",
        data={
            "headers": ["Group", "Formula", "Class", "Distinguishing test"],
            "rows": [
                ["Hydroxyl",   {"code": "-OH"},      "alcohol",         "reacts with Na, no gas with NaHCO3"],
                ["Carbonyl",   {"code": "C=O"},      "aldehyde/ketone", "Tollens (ald.), no Tollens (ket.)"],
                ["Carboxyl",   {"code": "-COOH"},    "carboxylic acid", "fizzes with NaHCO3"],
                ["Amine",      {"code": "-NH2"},     "amine",           "basic; turns red litmus blue"],
                ["Amide",      {"code": "-CONH2"},   "amide",           "neutral; hydrolyzes to acid + amine"],
                ["Ester",      {"code": "-COOR"},    "ester",           "fruity smell; hydrolyzes back"],
                ["Nitrile",    {"code": "-C≡N"},     "nitrile",         "reduces to primary amine"],
                ["Halide",     {"code": "-X"},       "alkyl halide",    "AgNO3 → AgX precipitate"],
            ],
        },
    ),
    RefSection(
        title="pKa quick reference",
        tag="acidity",
        kind="table",
        data={
            "headers": ["Acid", "pKa", "Conjugate base"],
            "rows": [
                ["HCl",            "-7",   "Cl⁻"],
                ["H2SO4 (1st)",    "-3",   "HSO4⁻"],
                ["H3O⁺",           "-1.7", "H2O"],
                ["RCOOH",          "~4-5", "RCOO⁻"],
                ["H2CO3",          "6.3",  "HCO3⁻"],
                ["NH4⁺",           "9.3",  "NH3"],
                ["HCO3⁻",          "10.3", "CO3²⁻"],
                ["ROH (alcohol)",  "~16",  "RO⁻"],
                ["NH3",            "~36",  "NH2⁻"],
                ["RH (alkane)",    "~50",  "R⁻"],
            ],
        },
    ),
    RefSection(
        title="Thermodynamics + kinetics core relations",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Gibbs free energy",  "formula": r"\Delta G = \Delta H - T\Delta S",
             "note": "spontaneous when ΔG < 0"},
            {"name": "Equilibrium",        "formula": r"\Delta G^\circ = -RT \ln K",
             "note": "K > 1 ↔ ΔG° < 0"},
            {"name": "Nernst equation",    "formula": r"E = E^\circ - \frac{RT}{nF}\ln Q",
             "note": "at 25°C: 0.0592/n in log form"},
            {"name": "Arrhenius rate",     "formula": r"k = A\,e^{-E_a / RT}",
             "note": "rate doubles ~every 10 K rise"},
            {"name": "Half-life (1st order)", "formula": r"t_{1/2} = \frac{\ln 2}{k}",
             "note": "independent of concentration"},
            {"name": "Ideal gas",          "formula": r"PV = nRT",
             "note": "R = 8.314 J/(mol·K) = 0.0821 L·atm/(mol·K)"},
        ],
    ),
    RefSection(
        title="Common reaction patterns",
        tag="reactions",
        kind="list",
        data=[
            {"term": "SN2",
             "explain": "1-step, backside attack, inverts stereochemistry, favored in primary substrates + polar aprotic solvent."},
            {"term": "SN1",
             "explain": "carbocation intermediate, racemization, favored in tertiary substrates + polar protic solvent."},
            {"term": "E2",
             "explain": "concerted anti-periplanar elimination, strong base + heat, Zaitsev product."},
            {"term": "E1",
             "explain": "carbocation then loss of β-H, weak base, often competes with SN1."},
            {"term": "Markovnikov addition",
             "explain": "H+ adds to the carbon with more H's already; carbocation goes to more substituted carbon."},
            {"term": "Anti-Markovnikov",
             "explain": "radical mechanism (HBr + peroxides); regiochemistry reversed."},
        ],
    ),
]


class ChemReferenceTool:
    name = "chem-reference"
    description = "Static chemistry reference card (functional groups, pKa, ΔG, kinetics)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_CHEM", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_CHEM=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match chem keywords")
        path = write_ref_page(packet_dir, "chem", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
