"""bart architecture tiers — pro / mid / local.

Three self-contained tiers live under this package; each owns its own
prompt set, agent roster, model routing, and quality-floor harness.

Pick a tier via ``get_tier("pro" | "mid" | "local")`` — the returned
``TierSpec`` exposes everything the orchestrator needs to wire up the
matching pipeline without leaking tier-specific knobs into the rest of
the codebase. Quality tests in :mod:`architectures.quality` apply
uniformly to every tier; a tier ships only when it passes them.
"""
from __future__ import annotations

from .registry import TierSpec, get_tier, list_tiers

__all__ = ["TierSpec", "get_tier", "list_tiers"]
