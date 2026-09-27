"""Read-only What-If Simulation_Engine for STOCKY (Requirement 16).

``simulate`` reuses the exact deterministic Detection_Engine functions —
:func:`app.detection.metrics.compute_metrics` and
:func:`app.detection.classify.classify` — to project the effect of hypothetical
inputs for a single SKU. It builds an **in-memory** snapshot from the stored SKU
data overlaid with the caller's overrides, computes the projected metrics and
classifications, and returns the current and projected views side by side for
comparison (Req 16.3).

Safety boundary
---------------
This service is strictly read-only (Req 16.2):

* It reads the SKU and its sales history through the ``sku_repo`` **read**
  functions only (:func:`sku_repo.get`, :func:`sku_repo.get_sales_units`).
* It never opens a write session, never calls any ``sku_repo`` inventory-write
  function, and never touches the Apply-Action Service. Neither of those write
  paths is imported here at all.
* Overrides are applied to a throwaway in-memory dataclass; the persisted
  ``skus`` / ``sales_history`` rows are left byte-for-byte unchanged. Nothing is
  persisted.

Determinism
-----------
Because the projection runs on the same pure metric/classify functions used at
ingest, simulating the same inputs twice yields identical results (Req 16.1).
The ``today`` parameter is injectable so the projected ``stockout_eta`` is
deterministic in tests. ``avg_daily_sales`` is the authoritative velocity input
to :func:`compute_metrics` and can itself be overridden via
:class:`~app.schemas.SimulationRequest` to project a hypothetical velocity
change directly; ``sales_history`` remains an override too, feeding only the
trend/anomaly rule in :func:`classify`.

Requirements: 16.1, 16.2, 16.3.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence

from sqlalchemy.orm import Session

from app.detection.classify import classify
from app.detection.metrics import compute_metrics
from app.models import Sku
from app.repositories import sku_repo
from app.schemas import MetricsView, SimulationRequest, SimulationResponse


@dataclass(frozen=True)
class _SkuSnapshot:
    """Plain-data, in-memory SKU snapshot fed to the pure Detection_Engine.

    Mirrors the fields :func:`app.detection.classify.classify` reads via its
    ``SkuLike`` protocol (``current_stock``, ``reorder_point``,
    ``lead_time_days``, ``sales_history``) plus ``unit_cost`` and
    ``avg_daily_sales`` (the authoritative velocity input to
    :func:`compute_metrics`) so the full set of hypothetical overrides has
    somewhere to land. It is a throwaway copy: never added to a session and
    never written back to the store.
    """

    current_stock: int
    reorder_point: int
    lead_time_days: int
    unit_cost: float
    avg_daily_sales: float
    sales_history: Sequence[int]


def _parse_classifications(raw: Optional[str]) -> List[str]:
    """Decode the SKU row's cached ``classifications`` JSON array into a list.

    The column stores a JSON array of label strings (see
    :func:`app.repositories.sku_repo.set_metrics`). Returns an empty list for a
    missing or unparseable value so the current view is always well-formed.
    """
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(value, list):
        return [str(label) for label in value]
    return []


def _current_view(row: Sku) -> MetricsView:
    """Build the CURRENT :class:`MetricsView` from the SKU's cached metrics.

    Reads the deterministic metrics + classifications already cached on the
    ``skus`` row at ingest (``sales_velocity``, ``days_of_cover``,
    ``stockout_eta``, ``no_recent_sales``, ``classifications``). No recompute is
    needed here — these values were produced by the same pure engine — so the
    current side reflects exactly what the rest of the app shows.
    """
    return MetricsView(
        sales_velocity=row.sales_velocity,
        days_of_cover=row.days_of_cover,
        stockout_eta=row.stockout_eta,
        no_recent_sales=bool(row.no_recent_sales),
        classifications=_parse_classifications(row.classifications),
    )


def _projected_view(
    snapshot: _SkuSnapshot,
    *,
    today: Optional[date],
) -> MetricsView:
    """Compute the PROJECTED :class:`MetricsView` for an in-memory snapshot.

    Runs the same pure Detection_Engine functions used at ingest —
    :func:`compute_metrics` then :func:`classify` — against the hypothetical
    snapshot. Nothing is persisted.
    """
    metrics = compute_metrics(
        snapshot.current_stock,
        snapshot.avg_daily_sales,
        today=today,
    )
    classifications = classify(
        snapshot,
        sales_velocity=metrics.sales_velocity,
        days_of_cover=metrics.days_of_cover,
    )
    return MetricsView(
        sales_velocity=metrics.sales_velocity,
        days_of_cover=metrics.days_of_cover,
        stockout_eta=metrics.stockout_eta,
        no_recent_sales=metrics.no_recent_sales,
        classifications=classifications,
    )


def _apply_overrides(row: Sku, units: Sequence[int], overrides: SimulationRequest) -> _SkuSnapshot:
    """Overlay only the provided override fields onto the stored SKU snapshot.

    Every field defaults to the stored value; a non-``None`` override replaces
    it in the in-memory copy. ``sales_history`` defaults to the SKU's stored
    daily units when not overridden. The result is a fresh :class:`_SkuSnapshot`
    — the persisted row is never touched.
    """
    sales_history = (
        list(overrides.sales_history)
        if overrides.sales_history is not None
        else list(units)
    )
    return _SkuSnapshot(
        current_stock=(
            overrides.current_stock
            if overrides.current_stock is not None
            else row.current_stock
        ),
        reorder_point=(
            overrides.reorder_point
            if overrides.reorder_point is not None
            else row.reorder_point
        ),
        lead_time_days=(
            overrides.lead_time_days
            if overrides.lead_time_days is not None
            else row.lead_time_days
        ),
        unit_cost=(
            overrides.unit_cost
            if overrides.unit_cost is not None
            else row.unit_cost
        ),
        avg_daily_sales=(
            overrides.avg_daily_sales
            if overrides.avg_daily_sales is not None
            else row.avg_daily_sales
        ),
        sales_history=sales_history,
    )


def simulate(
    session: Session,
    sku: str,
    overrides: SimulationRequest,
    *,
    today: Optional[date] = None,
) -> SimulationResponse:
    """Project the effect of hypothetical inputs for a SKU (Req 16.1-16.3).

    Reads the stored SKU and its sales history (read-only), builds an in-memory
    snapshot with only the provided overrides applied, computes projected
    metrics + classifications with the same pure Detection_Engine functions used
    at ingest, and returns the current and projected views side by side. Nothing
    is persisted (Req 16.2).

    Args:
        session: An open SQLAlchemy session, used for reads only. This function
            never writes, flushes a mutation, or commits.
        sku: The SKU id to simulate. Must already exist in the store.
        overrides: Hypothetical inputs. Only the non-``None`` fields override the
            stored SKU snapshot; the rest keep their stored values.
        today: Base date for the projected ``stockout_eta``, forwarded to
            :func:`compute_metrics`. Defaults to the current date; supply it for
            a deterministic ETA in tests.

    Returns:
        A :class:`SimulationResponse` with the SKU id and its ``current`` and
        ``projected`` :class:`MetricsView` values.

    Raises:
        KeyError: If ``sku`` does not exist in the store. Callers (e.g. the
            simulation router) translate this into a 404.
    """
    row = sku_repo.get(session, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")

    # Current side: reuse the metrics already cached on the row (produced by the
    # same deterministic engine at ingest).
    current = _current_view(row)

    # Projected side: overlay overrides onto a throwaway in-memory snapshot and
    # recompute with the pure engine. Reads are read-only; nothing is persisted.
    units = sku_repo.get_sales_units(session, sku)
    snapshot = _apply_overrides(row, units, overrides)
    projected = _projected_view(snapshot, today=today)

    return SimulationResponse(sku=sku, current=current, projected=projected)
