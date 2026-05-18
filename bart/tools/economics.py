"""Economics reference card. Static; zero LLM tokens.

Auto-fires for economics / micro / macro / econometrics / finance subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "economic", "econ ", "microeconom", "macroeconom", "econometric",
    "finance", "financial", "money and banking", "trade", "game theory",
    "market", "monetary", "fiscal",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="Core formulas",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Price elasticity of demand",
             "formula": r"E_d = \frac{\%\,\Delta Q_d}{\%\,\Delta P}",
             "note": "|E_d| > 1 elastic, < 1 inelastic, = 1 unit-elastic"},
            {"name": "GDP (expenditure approach)",
             "formula": r"\text{GDP} = C + I + G + (X - M)"},
            {"name": "Real vs. nominal",
             "formula": r"\text{Real GDP} = \frac{\text{Nominal GDP}}{\text{GDP deflator}}\times 100"},
            {"name": "Quantity theory of money",
             "formula": r"M V = P Y"},
            {"name": "Marginal propensity & multiplier",
             "formula": r"\text{Multiplier} = \frac{1}{1 - \text{MPC}} = \frac{1}{\text{MPS}}"},
            {"name": "Present value",
             "formula": r"PV = \frac{FV}{(1 + r)^n}"},
        ],
    ),
    RefSection(
        title="Microeconomics essentials",
        tag="micro",
        kind="list",
        data=[
            {"term": "Opportunity cost", "explain": "The value of the next-best forgone alternative — the true cost of any choice."},
            {"term": "Marginal analysis", "explain": "Optimize by acting while marginal benefit ≥ marginal cost; stop where MB = MC."},
            {"term": "Profit maximization", "explain": "A firm produces where MR = MC."},
            {"term": "Consumer / producer surplus", "explain": "Area between price and the demand (consumer) or supply (producer) curve."},
            {"term": "Deadweight loss", "explain": "Lost total surplus from a market not clearing at equilibrium (tax, price control, monopoly)."},
            {"term": "Comparative advantage", "explain": "Trade benefits both parties when each specializes in the good with lower opportunity cost."},
        ],
    ),
    RefSection(
        title="Macroeconomics essentials",
        tag="macro",
        kind="list",
        data=[
            {"term": "Fiscal policy", "explain": "Government spending & taxation to influence aggregate demand."},
            {"term": "Monetary policy", "explain": "Central bank control of money supply & interest rates."},
            {"term": "Inflation", "explain": "Sustained rise in the general price level; measured by CPI or the GDP deflator."},
            {"term": "Phillips curve", "explain": "Short-run inverse relationship between inflation and unemployment."},
            {"term": "Business cycle", "explain": "Expansion → peak → contraction → trough; output fluctuates around potential GDP."},
            {"term": "Crowding out", "explain": "Government borrowing raises interest rates, dampening private investment."},
        ],
    ),
    RefSection(
        title="Market structures",
        tag="structures",
        kind="table",
        data={
            "headers": ["Structure", "Firms", "Entry", "Price power", "Example"],
            "rows": [
                ["Perfect competition", "Many",  "Free",     "None (price taker)", "Agricultural commodities"],
                ["Monopolistic comp.",  "Many",  "Easy",     "Some",               "Restaurants, apparel"],
                ["Oligopoly",           "Few",   "Barriers", "Substantial",        "Airlines, telecom"],
                ["Monopoly",            "One",   "Blocked",  "High (price maker)", "Local utility"],
            ],
        },
    ),
]


class EconomicsReferenceTool:
    name = "economics-reference"
    description = "Static economics reference card (formulas, micro, macro, market structures)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_ECONOMICS", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_ECONOMICS=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match economics keywords")
        path = write_ref_page(packet_dir, "economics", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
