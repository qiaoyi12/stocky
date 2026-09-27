"""Safety-boundary data contracts for the agent layer.

This module defines the *only* things that cross into and out of the four LLM
agents. It is the heart of STOCKY's safety boundary (Req 9.1, 9.2):

    agent(context: AgentContext) -> AgentResult

``AgentContext`` is a frozen dataclass carrying **plain data only** — a snapshot
of the SKU, its Detection_Engine classifications, and prior agent outputs. It
holds no database session, repository, or engine, so an agent has no capability
to read or write the Inventory_Store even if its output attempted to. Agents
produce ``AgentResult`` (text plus parsed JSON) and, for the Manager, a
``ProposedAction`` validated against a closed enum of action kinds. Nothing here
is executable: outputs are inert data, never eval'd, never turned into SQL, never
dispatched as a command.

The deterministic Apply-Action Service (``services/apply_action.py``) is the sole
component that translates an approved ``ProposedAction`` into an inventory
mutation, and only after reviewer approval. This module deliberately imports no
db/repository/engine module — that absence is part of the guarantee.

Requirements: 9.1 (agents restricted to analysis/recommendation text only),
9.2 (agents cannot issue a write to the Inventory_Store).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


# --- Closed action enum -----------------------------------------------------


class ActionKind(str, Enum):
    """The closed set of inventory actions the deterministic apply service knows.

    A ``str`` enum so it serializes/deserializes cleanly as JSON. Any value
    outside this set is not a valid action; Manager output that does not map to
    one of these is coerced to ``NO_ACTION`` by the orchestrator (Req 9.4). The
    schema-layer ``schemas.ActionKind`` mirrors this set for the API contract.
    """

    REORDER = "reorder"  # increase incoming/target stock by quantity
    ADJUST_REORDER = "adjust_reorder"  # set reorder_point to new value
    MARKDOWN = "markdown"  # flag for markdown (overstock / slow-mover)
    NO_ACTION = "no_action"  # record decision, change nothing


# --- Proposed action (data only, inert) -------------------------------------


@dataclass(frozen=True)
class ProposedAction:
    """A structured, executable-shape proposal produced by the Manager agent.

    Frozen and data-only: it *describes* an action but performs nothing. Only the
    deterministic Apply-Action Service reads it, and only after approval. The
    fields form the closed schema the apply service switches on — any field the
    Manager emits outside this schema is dropped when the orchestrator validates
    the JSON into this type.

    - ``kind``: one of the closed ``ActionKind`` values.
    - ``sku``: the single target SKU; an action affects only this SKU.
    - ``quantity``: units for ``REORDER`` (``None`` otherwise).
    - ``new_reorder_point``: target for ``ADJUST_REORDER`` (``None`` otherwise).
    """

    kind: ActionKind
    sku: str
    quantity: Optional[int] = None
    new_reorder_point: Optional[int] = None


# --- SKU snapshot (plain data) ----------------------------------------------


@dataclass(frozen=True)
class SkuSnapshot:
    """An immutable, plain-data copy of a SKU and its computed metrics.

    A snapshot, not a live row: it is detached from any database session or ORM
    object, so passing it to an agent grants no write path back to the store.
    ``days_of_cover``/``stockout_eta`` are ``None`` for the zero-velocity edge
    (Req 2.5). ``classifications`` are the Detection_Engine labels attached to
    the SKU.
    """

    sku: str
    name: str
    category: str
    current_stock: int
    reorder_point: int
    lead_time_days: int
    unit_cost: float
    selling_price: float = 0.0
    supplier_name: str = ""
    avg_daily_sales: float = 0.0
    last_sold_date: Optional[str] = None
    sales_history: List[int] = field(default_factory=list)
    sales_velocity: Optional[float] = None
    days_of_cover: Optional[float] = None
    stockout_eta: Optional[str] = None
    no_recent_sales: bool = False
    classifications: List[str] = field(default_factory=list)


# --- Agent result -----------------------------------------------------------


@dataclass(frozen=True)
class AgentResult:
    """The output of a single agent: free text plus optional parsed JSON.

    ``text`` is the agent's written analysis/recommendation (Req 9.1). ``data``
    is the parsed JSON object an agent emits (the Manager uses it for the
    structured action); it is stored as inert data and never executed (Req 9.4).
    ``action`` is populated only by the Manager, carrying the validated
    ``ProposedAction`` (or the ``NO_ACTION`` fallback).
    """

    text: str
    data: Optional[Dict[str, Any]] = None
    action: Optional[ProposedAction] = None


# --- Agent context (frozen, plain data, no DB handle) -----------------------


@dataclass(frozen=True)
class AgentContext:
    """Immutable input passed to every agent — plain data, no capabilities.

    Carries the SKU snapshot, its classifications, and the outputs of prior
    agents (``prior``, keyed by step name, chained forward as the sequence runs).
    It deliberately holds no database session, repository, or engine: an agent
    receiving an ``AgentContext`` has no handle through which to read or mutate
    the Inventory_Store (Req 9.1, 9.2).

    Being frozen, adding a prior output returns a *new* context via
    ``with_output`` rather than mutating in place, which keeps each agent's input
    an independent immutable value.
    """

    sku: SkuSnapshot
    classifications: List[str] = field(default_factory=list)
    prior: Mapping[str, AgentResult] = field(default_factory=dict)

    def with_output(self, step: str, result: AgentResult) -> "AgentContext":
        """Return a new context with ``result`` recorded under ``step``.

        Does not mutate ``self`` (the dataclass is frozen). Used by the
        orchestrator to chain each agent's output forward to the next agent
        (Req 8.2) without ever sharing a mutable structure across steps.
        """

        merged: Dict[str, AgentResult] = dict(self.prior)
        merged[step] = result
        return replace(self, prior=merged)
