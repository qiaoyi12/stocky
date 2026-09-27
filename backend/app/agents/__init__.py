"""Agent layer for STOCKY.

Houses the four sequential LLM agents (Detective -> Forecast -> Strategy ->
Manager), their LLM client wrapper, and the read-only orchestrator. The safety
boundary is enforced structurally: this package's data contracts
(:mod:`app.agents.context`) carry plain data only, and no module in this package
imports the inventory-write repository functions or the deterministic
Apply-Action Service.

The data-only contracts are re-exported here for convenience.
"""

from __future__ import annotations

from app.agents.context import (
    ActionKind,
    AgentContext,
    AgentResult,
    ProposedAction,
    SkuSnapshot,
)

__all__ = [
    "ActionKind",
    "AgentContext",
    "AgentResult",
    "ProposedAction",
    "SkuSnapshot",
]
