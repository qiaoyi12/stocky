"""Pydantic request/response schemas for STOCKY.

These models define the shapes exchanged over the REST API and the parsed CSV
row produced by the ingest layer. They are pure data contracts: no I/O, no LLM,
no database access. Field names mirror the Database Schema and API Endpoints
sections of the design so responses map cleanly onto stored rows.

The closed set of inventory action kinds (``ActionKind``) matches the deterministic
Apply-Action Service's executable surface. Recommendation lifecycle states are
likewise a closed set (``pending``/``approved``/``rejected``/``applied``).

Requirements: 1.5 (upload accepted/rejected counts), 14.1 (inventory listing).
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# --- Closed enums -----------------------------------------------------------


class ActionKind(str, Enum):
    """The only inventory actions the deterministic apply service can execute.

    Mirrors ``agents.context.ActionKind``; duplicated at the schema layer so the
    API contract is self-contained and validates incoming/outgoing action kinds
    against the closed set (Req 9.4 — anything outside this set is not executable).
    """

    REORDER = "reorder"
    ADJUST_REORDER = "adjust_reorder"
    MARKDOWN = "markdown"
    NO_ACTION = "no_action"


class RecommendationStatus(str, Enum):
    """Recommendation lifecycle states (Req 10-12)."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"


class CaseStatus(str, Enum):
    """Investigation case states."""

    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"


# --- CSV ingest -------------------------------------------------------------


class SkuRow(BaseModel):
    """A single validated CSV row parsed by the ingest layer (Req 1.1).

    ``avg_daily_sales`` is the authoritative Sales_Velocity source (CSV-provided,
    not derived). ``sales_history`` is kept only for the trend/anomaly
    window-halves detection rule — it is no longer used to compute velocity. The
    numeric fields are the ones the parser validates as numeric (Req 1.4).
    """

    sku: str
    name: str
    category: str
    current_stock: int
    reorder_point: int
    lead_time_days: int
    unit_cost: float
    selling_price: float
    supplier_name: str
    avg_daily_sales: float
    sales_history: List[int] = Field(default_factory=list)
    last_sold_date: str


class RejectedRow(BaseModel):
    """A row rejected during ingest, carrying its source row number and reason (Req 1.4)."""

    row: int
    reason: str


class UploadResult(BaseModel):
    """Result of a CSV upload (Req 1.5).

    ``accepted``/``rejected_count`` are the counts required by Req 1.5;
    ``rejected`` lists per-row reasons. ``missing_column`` is set only when the
    whole upload was rejected for a missing required column (Req 1.3), in which
    case no rows are accepted.
    """

    accepted: int
    rejected_count: int
    rejected: List[RejectedRow] = Field(default_factory=list)
    missing_column: Optional[str] = None


# --- Inventory --------------------------------------------------------------


class InventoryItem(BaseModel):
    """A SKU with its cached deterministic metrics and classifications.

    Used for both the inventory list (Req 14.1, 14.2) and single-SKU detail
    (Req 14.3). ``days_of_cover``/``stockout_eta`` are nullable — undefined when
    Sales_Velocity is zero (Req 2.5).
    """

    sku: str
    name: str
    category: str
    current_stock: int
    reorder_point: int
    lead_time_days: int
    unit_cost: float
    selling_price: float
    supplier_name: str
    avg_daily_sales: float
    last_sold_date: Optional[str] = None
    sales_velocity: Optional[float] = None
    days_of_cover: Optional[float] = None
    stockout_eta: Optional[str] = None
    no_recent_sales: bool = False
    classifications: List[str] = Field(default_factory=list)
    updated_at: Optional[str] = None


class InventoryListResponse(BaseModel):
    """Response for ``GET /api/inventory`` (Req 14.1, 14.2)."""

    items: List[InventoryItem] = Field(default_factory=list)


# --- Agent case -------------------------------------------------------------


class RecommendationResponse(BaseModel):
    """A persisted recommendation and its lifecycle status.

    Fields mirror the ``recommendations`` table. ``quantity`` and
    ``new_reorder_point`` are populated per action kind (reorder /
    adjust_reorder respectively); ``rationale`` is the Manager's supporting
    text stored as inert text only (Req 7.2, 9.4).
    """

    id: int
    case_id: int
    sku: str
    action_kind: ActionKind
    quantity: Optional[int] = None
    new_reorder_point: Optional[int] = None
    rationale: str
    status: RecommendationStatus = RecommendationStatus.PENDING
    created_at: Optional[str] = None
    decided_at: Optional[str] = None
    applied_at: Optional[str] = None


class CaseResponse(BaseModel):
    """The four-agent case for a SKU (Req 15.1, 15.2).

    Presents each agent's output text (or ``None`` when the step did not run),
    the case status, the failed step if any, and the associated recommendation
    when one exists.
    """

    id: int
    sku: str
    status: CaseStatus
    failed_step: Optional[str] = None
    detective_out: Optional[str] = None
    forecast_out: Optional[str] = None
    strategy_out: Optional[str] = None
    manager_out: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None
    recommendation: Optional[RecommendationResponse] = None


class RunInvestigationResponse(BaseModel):
    """Response for ``POST /api/investigations/{sku}/run`` — case id + status."""

    case_id: int
    status: CaseStatus
    failed_step: Optional[str] = None


# --- Review / apply ---------------------------------------------------------


class ReviewResult(BaseModel):
    """Result of an approve/reject/apply action.

    ``approve`` returns ``{status, applied, error?}`` (design API table): approval
    sets status to approved then invokes apply; on success status becomes applied
    (Req 11.2), on failure it stays approved with an error (Req 11.3). Reject
    returns the rejected status with ``applied=False``.
    """

    status: RecommendationStatus
    applied: bool = False
    error: Optional[str] = None


# --- Cost of overstock ------------------------------------------------------


class CapitalTiedUpItem(BaseModel):
    """Capital tied up in the excess stock of one overstock SKU.

    ``excess_units`` is ``max(0, current_stock - reorder_point)`` and
    ``capital_tied_up`` is that excess valued at ``unit_cost`` — the dollar
    figure sitting idle as unnecessary storage cost. Both are always >= 0.
    """

    sku: str
    excess_units: int
    capital_tied_up: float


class CapitalTiedUp(BaseModel):
    """Cost-of-overstock summary: total capital tied up and a per-SKU breakdown.

    ``total`` is the sum of ``capital_tied_up`` across overstock SKUs (always
    >= 0); ``by_sku`` lists only overstock SKUs, ordered by ``capital_tied_up``
    descending.
    """

    total: float = 0.0
    by_sku: List[CapitalTiedUpItem] = Field(default_factory=list)


# --- Dashboard --------------------------------------------------------------


class DashboardResponse(BaseModel):
    """Response for ``GET /api/dashboard`` (Req 13.1-13.3).

    ``condition_counts`` maps each detected condition label to the number of
    SKUs carrying it; ``lifecycle_counts`` maps each recommendation status to
    its count; ``stockout_risk`` is the prioritised group of at-risk SKUs.
    """

    condition_counts: dict[str, int] = Field(default_factory=dict)
    lifecycle_counts: dict[str, int] = Field(default_factory=dict)
    stockout_risk: List[InventoryItem] = Field(default_factory=list)
    overstock_capital_tied_up: CapitalTiedUp = Field(default_factory=CapitalTiedUp)


# --- Impact -----------------------------------------------------------------


class ImpactResponse(BaseModel):
    """Response for ``GET /api/impact`` (Req 17.1, 17.2).

    ``outcome_counts`` are recommendation counts by lifecycle status across the
    dataset; ``condition_counts`` are aggregate per-condition metrics across all
    SKUs. ``overstock_capital_tied_up`` is the current cost-of-overstock figure
    (capital sitting idle in excess stock) across the dataset.
    """

    outcome_counts: dict[str, int] = Field(default_factory=dict)
    condition_counts: dict[str, int] = Field(default_factory=dict)
    overstock_capital_tied_up: CapitalTiedUp = Field(default_factory=CapitalTiedUp)


# --- Simulation -------------------------------------------------------------


class MetricsView(BaseModel):
    """The deterministic metrics + classifications for a SKU snapshot.

    Reused for both the current and projected sides of a simulation comparison.
    ``days_of_cover``/``stockout_eta`` are nullable for the zero-velocity edge
    (Req 2.5).
    """

    sales_velocity: Optional[float] = None
    days_of_cover: Optional[float] = None
    stockout_eta: Optional[str] = None
    no_recent_sales: bool = False
    classifications: List[str] = Field(default_factory=list)


class SimulationRequest(BaseModel):
    """Hypothetical overrides for ``POST /api/simulation/{sku}`` (Req 16.1).

    All fields optional — only the provided ones override the stored SKU snapshot.
    ``sales_history`` allows simulating a hypothetical daily sales rate/series.
    """

    current_stock: Optional[int] = None
    reorder_point: Optional[int] = None
    lead_time_days: Optional[int] = None
    unit_cost: Optional[float] = None
    avg_daily_sales: Optional[float] = None
    sales_history: Optional[List[int]] = None


class SimulationResponse(BaseModel):
    """Current vs projected metrics side by side (Req 16.3). Nothing is persisted (Req 16.2)."""

    sku: str
    current: MetricsView
    projected: MetricsView


# --- Datasets ----------------------------------------------------------------


class DatasetSummary(BaseModel):
    """A dataset's metadata for ``GET /api/datasets`` (Req 5.1, 5.2).

    ``sku_count`` is computed from the count of ``skus`` rows carrying this
    dataset's id; ``is_active`` reflects whether this is the current Active_Dataset.
    """

    id: int
    filename: str
    display_name: str
    uploaded_at: str
    sku_count: int
    is_active: bool


class DatasetRenameRequest(BaseModel):
    """Request body for ``PATCH /api/datasets/{id}`` (Req 7.1)."""

    display_name: str
