"""Computer science / algorithms reference card. Static; zero LLM tokens.

Auto-fires for CS / algorithms / data structures / discrete-math subjects.
"""
from __future__ import annotations

import os
from pathlib import Path

from .domain import RefSection, subject_matches, write_ref_page


KEYWORDS = (
    "computer science", "cs ", "algorithm", "data struct", "discrete math",
    "automata", "computational", "complexity", "compiler", "operating system",
)


SECTIONS: list[RefSection] = [
    RefSection(
        title="Big-O of common operations",
        tag="complexity",
        kind="table",
        data={
            "headers": ["Structure", "Access", "Search", "Insert", "Delete"],
            "rows": [
                ["Array",            "O(1)",     "O(n)",     "O(n)",     "O(n)"],
                ["Sorted array",     "O(1)",     "O(log n)", "O(n)",     "O(n)"],
                ["Linked list",      "O(n)",     "O(n)",     "O(1) head","O(1) head"],
                ["Stack / Queue",    "O(1) top", "O(n)",     "O(1)",     "O(1)"],
                ["Hash table (avg)", "—",        "O(1)",     "O(1)",     "O(1)"],
                ["Hash table (worst)","—",       "O(n)",     "O(n)",     "O(n)"],
                ["Binary search tree (avg)","O(log n)","O(log n)","O(log n)","O(log n)"],
                ["Balanced BST",     "O(log n)", "O(log n)", "O(log n)", "O(log n)"],
                ["Heap",             "O(1) min", "O(n)",     "O(log n)", "O(log n)"],
                ["Trie",             "—",        "O(k)",     "O(k)",     "O(k)"],
            ],
        },
    ),
    RefSection(
        title="Sorting algorithms",
        tag="sorting",
        kind="table",
        data={
            "headers": ["Algorithm", "Best", "Average", "Worst", "Space", "Stable?"],
            "rows": [
                ["Bubble",     "O(n)",       "O(n²)",      "O(n²)",      "O(1)",     "yes"],
                ["Insertion",  "O(n)",       "O(n²)",      "O(n²)",      "O(1)",     "yes"],
                ["Selection",  "O(n²)",      "O(n²)",      "O(n²)",      "O(1)",     "no"],
                ["Merge",      "O(n log n)", "O(n log n)", "O(n log n)", "O(n)",     "yes"],
                ["Quick",      "O(n log n)", "O(n log n)", "O(n²)",      "O(log n)", "no"],
                ["Heap",       "O(n log n)", "O(n log n)", "O(n log n)", "O(1)",     "no"],
                ["Counting",   "O(n+k)",     "O(n+k)",     "O(n+k)",     "O(k)",     "yes"],
                ["Radix",      "O(nk)",      "O(nk)",      "O(nk)",      "O(n+k)",   "yes"],
            ],
        },
    ),
    RefSection(
        title="Graph algorithms",
        tag="graphs",
        kind="table",
        data={
            "headers": ["Algorithm", "Use", "Time"],
            "rows": [
                ["BFS",         "shortest path (unweighted)",     "O(V+E)"],
                ["DFS",         "components, cycle detect, topo", "O(V+E)"],
                ["Dijkstra",    "single-source shortest (≥0 wts)","O((V+E) log V)"],
                ["Bellman-Ford","negative weights, neg-cycle det","O(VE)"],
                ["Floyd-Warshall","all-pairs shortest path",      "O(V³)"],
                ["Kruskal",     "MST (sorted edges + DSU)",       "O(E log E)"],
                ["Prim",        "MST (priority queue)",            "O((V+E) log V)"],
                ["Tarjan SCC",  "strongly connected components",  "O(V+E)"],
            ],
        },
    ),
    RefSection(
        title="Recurrence-relation cookbook",
        tag="formulas",
        kind="formulas",
        data=[
            {"name": "Master theorem (a≥1, b>1)",
             "formula": r"T(n) = a\,T(n/b) + f(n)",
             "note": "compare f(n) to n^{log_b a}: smaller→that exponent dominates; larger (regular)→f(n); equal→multiply by log n"},
            {"name": "Divide & conquer (merge sort)",
             "formula": r"T(n) = 2T(n/2) + O(n) \;\Rightarrow\; T(n) = O(n \log n)"},
            {"name": "Linear recurrence (Fibonacci)",
             "formula": r"F_n = F_{n-1} + F_{n-2} \;\Rightarrow\; F_n = O(\varphi^n)",
             "note": r"\varphi = (1+\sqrt 5)/2"},
            {"name": "Tree height (balanced)",
             "formula": r"h = \lfloor \log_2 n \rfloor"},
        ],
    ),
    RefSection(
        title="Common asymptotic identities",
        tag="bounds",
        kind="list",
        data=[
            {"term": "Sum 1..n",            "explain": "n(n+1)/2  (Θ(n²))"},
            {"term": "Sum of squares",      "explain": "n(n+1)(2n+1)/6  (Θ(n³))"},
            {"term": "Sum of geometric",    "explain": "(r^(n+1)-1)/(r-1) for r ≠ 1"},
            {"term": "Harmonic H_n",        "explain": "ln n + γ + O(1/n)  (~ ln n)"},
            {"term": "log₂(n!)",            "explain": "Θ(n log n)  (Stirling)"},
            {"term": "n choose k upper bound", "explain": "(en/k)^k  ≤  C(n,k)  ≤ n^k / k!"},
        ],
    ),
]


class CSReferenceTool:
    name = "cs-reference"
    description = "Static CS reference card (Big-O, sorting, graphs, recurrences)"

    def run(self, packet_dir: Path, manifest: dict):
        from . import ToolResult
        cfg = manifest.get("config") or {}
        subject = cfg.get("subject", "")
        forced = os.environ.get("BART_TOOL_CS", "")
        if forced == "0":
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail="disabled via BART_TOOL_CS=0")
        if forced != "1" and not subject_matches(subject, KEYWORDS):
            return ToolResult(name=self.name, success=False, output_paths=[],
                              detail=f"subject '{subject}' did not match CS keywords")
        path = write_ref_page(packet_dir, "cs", subject, SECTIONS)
        return ToolResult(name=self.name, success=True, output_paths=[path],
                          detail=f"{len(SECTIONS)} reference sections")
