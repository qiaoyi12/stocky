"""Chaos Mode service for STOCKY (Requirement 18, NICE TO HAVE).

Chaos Mode lets a Reviewer inject synthetic disruptive events (a demand spike, a
demand drop, or a supply delay) for a demonstration, then shows how the
Detection_Engine reclassifies the affected SKUs (Req 18.1, 18.2).

Working-copy isolation (Req 18.3)
---------------------------------
This module implements **approach (a)**: chaos events act on an *in-memory
working copy* of each affected SKU. Nothing here writes to the ``skus`` or
``sales_history`` source rows. The service reads the canonical dataset through
the ``sku_repo`` **read** functions only, applies the event to plain-data
snapshots, reruns the pure Detection_Engine functions
(:func:`app.detection.metrics.compute_metrics` then
:func:`app.detection.classify.classify`) on those snapshots, and returns the
projected before/after results. It never imports or calls an inventory-write
function, never calls :func:`sku_repo.set_metrics`, and never commits — so the
originally uploaded source data is guaranteed untouched (Req 18.3).

This is the same read-only pattern the What-If Simulation_Engine uses: reuse the
deterministic detection primitives against an in-memory snapshot rather than
persisting anything.

The result shape is defined inline here (small Pydantic models) so ``schemas.py``
stays untouched, as required by the task.

Requirements: 18.1, 18.2, 18.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Literal, Optional, Sequence

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.detection.classify import classify
from app.detection.metrics import compute_metrics
from app.repositories import sku_repo

# Supported synthetic event kinds (Req 18.1). Kept as a closed set so the
# effect of each scenario is deterministic and documented.
ScenarioKind = Literal["demand_spike", "demand_drop", "supply_delay"]


class ChaosRequest(BaseModel):
    """A Chaos Mode injection request.

    Attributes:
        scenario: Which synthetic disruptive event to inject.
        intensity: Strength of the event, a positive multiplier/scale. Its exact
            meaning depends on the scenario (see :func:`_perturb`). Defaults to a
            moderate value so a bare ``{"scenario": ...}`` body works.
        skus: Optional explicit list of SKU ids to affect. When omitted, the
            event is applied to every SKU in the dataset.
    """

    scenario: ScenarioKind
    intensity: float = Field(default=2.0, gt=0)
    skus: Optional[List[str]] = None


class SkuChaosResult(BaseModel):
    """Before/after projection for a single affected SKU (working copy only)."""

    sku: str
    # Snapshot of the (working-copy) inputs the event produced.
    current_stock_before: int
    current_stock_after: int
    lead_time_days_before: int
    lead_time_days_after: int
    sales_history_before: List[int]
    sales_history_after: List[int]
    # Recomputed metrics.
    sales_velocity_before: float
    sales_velocity_after: float
    days_of_cover_before: Optional[float]
    days_of_cover_after: Optional[float]
    # Reclassification (Req 18.2).
    classifications_before: List[str]
    classifications_after: List[str]


class ChaosResponse(BaseModel):
    """The full Chaos Mode result.

    ``source_unchanged`` is always ``True`` for this service: it is a working
    copy computation that never persists, documenting the Req 18.3 guarantee in
    the response itself.
    """

    scenario: ScenarioKind
    intensity: float
    affected_count: int
    results: List[SkuChaosResult]
    source_unchanged: bool = True


@dataclass(frozen=True)
class _Snapshot:
    """Immutable in-memory working copy of one SKU's detection inputs.

    Exposes exactly the attributes the classifier's ``SkuLike`` protocol reads
    (``current_stock``, ``reorder_point``, ``lead_time_days``,
    ``sales_history``) plus ``avg_daily_sales`` (the authoritative velocity
    input to :func:`compute_metrics`), so it can be fed straight into the pure
    Detection_Engine functions. Nothing derived from this is written back.
    """

    current_stock: int
    reorder_point: int
    lead_time_days: int
    avg_daily_sales: float
    sales_history: List[int]


def _snapshot_from_row(session: Session, sku: str) -> _Snapshot:
    """Build a working-copy snapshot from the canonical (read-only) source rows."""
    row = sku_repo.get(session, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")
    units = sku_repo.get_sales_units(session, sku)
    return _Snapshot(
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        avg_daily_sales=row.avg_daily_sales,
        sales_history=list(units),
    )


def _perturb(snapshot: _Snapshot, scenario: ScenarioKind, intensity: float) -> _Snapshot:
    """Return a NEW snapshot with the synthetic event applied (Req 18.1).

    Pure transform on the in-memory working copy; the input snapshot is never
    mutated. Event semantics:

    * ``demand_spike`` — recent daily sales scaled up by ``intensity`` (demand
      surges), so velocity rises and days-of-cover falls.
    * ``demand_drop``  — recent daily sales scaled down by ``intensity`` (demand
      collapses), so velocity falls and days-of-cover rises.
    * ``supply_delay`` — lead time stretched by ``intensity`` (replenishment is
      delayed), which raises stockout risk relative to days-of-cover.

    Values are kept non-negative and integral where the source is integral so the
    working copy stays a plausible dataset.
    """
    if scenario == "demand_spike":
        new_history = [max(0, round(u * intensity)) for u in snapshot.sales_history]
        return _Snapshot(
            current_stock=snapshot.current_stock,
            reorder_point=snapshot.reorder_point,
            lead_time_days=snapshot.lead_time_days,
            avg_daily_sales=max(0.0, snapshot.avg_daily_sales * intensity),
            sales_history=new_history,
        )
    if scenario == "demand_drop":
        new_history = [max(0, round(u / intensity)) for u in snapshot.sales_history]
        return _Snapshot(
            current_stock=snapshot.current_stock,
            reorder_point=snapshot.reorder_point,
            lead_time_days=snapshot.lead_time_days,
            avg_daily_sales=max(0.0, snapshot.avg_daily_sales / intensity),
            sales_history=new_history,
        )
    if scenario == "supply_delay":
        new_lead = max(0, round(snapshot.lead_time_days * intensity))
        return _Snapshot(
            current_stock=snapshot.current_stock,
            reorder_point=snapshot.reorder_point,
            lead_time_days=new_lead,
            avg_daily_sales=snapshot.avg_daily_sales,
            sales_history=list(snapshot.sales_history),
        )
    # ``ScenarioKind`` is closed, but guard defensively for forward changes.
    raise ValueError(f"unknown chaos scenario: {scenario!r}")


def _classify_snapshot(snapshot: _Snapshot, today: Optional[date]) -> Dict:
    """Run the pure Detection_Engine on a snapshot and return its metrics + labels."""
    metrics = compute_metrics(snapshot.current_stock, snapshot.avg_daily_sales, today=today)
    labels = classify(
        snapshot,
        sales_velocity=metrics.sales_velocity,
        days_of_cover=metrics.days_of_cover,
    )
    return {
        "sales_velocity": metrics.sales_velocity,
        "days_of_cover": metrics.days_of_cover,
        "classifications": labels,
    }


def apply_chaos(
    session: Session,
    scenario: ChaosRequest,
    *,
    today: Optional[date] = None,
) -> ChaosResponse:
    """Inject a synthetic event onto a working copy and reclassify (Req 18.1-18.3).

    For each affected SKU this reads the canonical row through the ``sku_repo``
    read path, builds an in-memory :class:`_Snapshot`, classifies the *before*
    state, applies the event with :func:`_perturb` to produce a NEW snapshot,
    classifies the *after* state, and records both. It writes nothing: the
    ``skus`` and ``sales_history`` source rows are never touched, so the
    originally uploaded data is unchanged (Req 18.3).

    Args:
        session: An open SQLAlchemy session. Used only for repository *reads*;
            no commit is issued and no write function is called.
        scenario: The :class:`ChaosRequest` describing the event, its intensity,
            and (optionally) the target SKUs. When ``scenario.skus`` is omitted,
            every SKU in the dataset is affected.
        today: Base date for the deterministic ``stockout_eta``/days-of-cover
            projection, forwarded to :func:`compute_metrics`. Defaults to the
            current date; supply it for deterministic output in tests.

    Returns:
        A :class:`ChaosResponse` with a per-SKU before/after
        :class:`SkuChaosResult` for every affected SKU.

    Raises:
        KeyError: If an explicitly requested SKU id does not exist.
    """
    if scenario.skus is not None:
        target_ids: Sequence[str] = scenario.skus
    else:
        target_ids = [row.sku for row in sku_repo.list_all(session)]

    results: List[SkuChaosResult] = []
    for sku_id in target_ids:
        before = _snapshot_from_row(session, sku_id)
        after = _perturb(before, scenario.scenario, scenario.intensity)

        before_metrics = _classify_snapshot(before, today)
        after_metrics = _classify_snapshot(after, today)

        results.append(
            SkuChaosResult(
                sku=sku_id,
                current_stock_before=before.current_stock,
                current_stock_after=after.current_stock,
                lead_time_days_before=before.lead_time_days,
                lead_time_days_after=after.lead_time_days,
                sales_history_before=list(before.sales_history),
                sales_history_after=list(after.sales_history),
                sales_velocity_before=before_metrics["sales_velocity"],
                sales_velocity_after=after_metrics["sales_velocity"],
                days_of_cover_before=before_metrics["days_of_cover"],
                days_of_cover_after=after_metrics["days_of_cover"],
                classifications_before=before_metrics["classifications"],
                classifications_after=after_metrics["classifications"],
            )
        )

    return ChaosResponse(
        scenario=scenario.scenario,
        intensity=scenario.intensity,
        affected_count=len(results),
        results=results,
    )
