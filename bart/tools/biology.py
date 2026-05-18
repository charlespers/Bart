"""Biology reference card. Static; zero LLM tokens.

Auto-fires for biology / cell / molecular / genetics / physiology subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "biology", "bio ", "molecular bio", "cell bio", "genetics", "genomic",
    "physiology", "anatomy", "ecology", "evolution", "microbio",
    "neuroscience", "botany", "zoology", "immunolog",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="The four biological macromolecules",
        tag="table",
        kind="table",
        data={
            "headers": ["Class", "Monomer", "Key bond", "Primary roles"],
            "rows": [
                ["Carbohydrate", "Monosaccharide", "Glycosidic", "Energy, structure (cellulose)"],
                ["Lipid",        "(not a polymer)", "Ester",     "Membranes, energy store, signaling"],
                ["Protein",      "Amino acid",     "Peptide",   "Catalysis, transport, structure"],
                ["Nucleic acid", "Nucleotide",     "Phospho­diester", "Information storage & transfer"],
            ],
        },
    ),
    RefSection(
        title="Central dogma & molecular biology",
        tag="core",
        kind="list",
        data=[
            {"term": "Central dogma", "explain": "DNA →(replication) DNA →(transcription) RNA →(translation) protein."},
            {"term": "Base pairing", "explain": "A–T (DNA) / A–U (RNA), G–C. Antiparallel strands; 5′→3′ synthesis."},
            {"term": "Transcription", "explain": "RNA polymerase reads template 3′→5′, builds mRNA 5′→3′; occurs in the nucleus (eukaryotes)."},
            {"term": "Translation", "explain": "Ribosome reads mRNA codons 5′→3′; tRNA delivers amino acids; start = AUG, stop = UAA/UAG/UGA."},
            {"term": "Codon", "explain": "3 nucleotides → 1 amino acid; 64 codons, 20 amino acids → the code is degenerate."},
            {"term": "Gene regulation", "explain": "Promoters, enhancers, repressors, and operons (prokaryotes) control when/how much a gene is expressed."},
        ],
    ),
    RefSection(
        title="Eukaryotic organelles",
        tag="cell",
        kind="table",
        data={
            "headers": ["Organelle", "Function"],
            "rows": [
                ["Nucleus",            "Houses DNA; site of transcription"],
                ["Ribosome",           "Protein synthesis (translation)"],
                ["Rough ER",           "Protein folding & modification"],
                ["Smooth ER",          "Lipid synthesis, detoxification"],
                ["Golgi apparatus",    "Sorts, modifies, ships proteins"],
                ["Mitochondrion",      "ATP production (cellular respiration)"],
                ["Chloroplast",        "Photosynthesis (plants/algae)"],
                ["Lysosome",           "Digestion, recycling of macromolecules"],
            ],
        },
    ),
    RefSection(
        title="Genetics quick rules",
        tag="genetics",
        kind="list",
        data=[
            {"term": "Monohybrid cross", "explain": "Heterozygous × heterozygous (Aa × Aa) → 3:1 phenotype, 1:2:1 genotype."},
            {"term": "Dihybrid cross", "explain": "AaBb × AaBb → 9:3:3:1 phenotype (independent assortment)."},
            {"term": "Test cross", "explain": "Unknown × homozygous recessive — reveals the unknown genotype."},
            {"term": "Hardy–Weinberg", "explain": "p² + 2pq + q² = 1 and p + q = 1; allele frequencies stable absent evolution."},
            {"term": "Sex linkage", "explain": "X-linked recessive traits appear far more often in males (XY)."},
        ],
    ),
    RefSection(
        title="Energy in the cell",
        tag="energetics",
        kind="formulas",
        data=[
            {"name": "Cellular respiration (net)",
             "formula": r"\ce{C6H12O6 + 6 O2 -> 6 CO2 + 6 H2O}",
             "note": "≈ 30–32 ATP per glucose in eukaryotes"},
            {"name": "Photosynthesis (net)",
             "formula": r"\ce{6 CO2 + 6 H2O ->[light] C6H12O6 + 6 O2}"},
            {"name": "Hardy–Weinberg equilibrium",
             "formula": r"p^2 + 2pq + q^2 = 1,\qquad p + q = 1"},
        ],
    ),
]


class BiologyReferenceTool:
    name = "biology-reference"
    description = "Static biology reference card (macromolecules, central dogma, cells, genetics)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_BIOLOGY", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_BIOLOGY=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match biology keywords")
        path = write_ref_page(packet_dir, "biology", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
