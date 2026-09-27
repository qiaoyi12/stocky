"""Dashboard aggregation service for STOCKY (Requirement 13).

Read-only aggregation over the stored inventory + recommendations that powers
``GET /api/dashboard`` (task 7.2). Three summaries are produced (design "API
Endpoints", Dashboard row):

* **condition_counts** — for each detected condition label, the number of SKUs
  carrying that label, recomputed independently from the stored
  ``classifications`` JSON on each ``skus`` row (Req 13.1).
* **lifecycle_counts** — for each recommendation lifecycle status, the number of
  recommendations in that status (Req 13.2).
* **stockout_risk** — the SKUs classified ``stockout_risk`` presented as a
  prioritised group, soonest-to-stock-out first (Req 13.3).

This module is a pure read path: it goes only through the ``sku_repo`` /
``recommendation_repo`` *read* functions and never imports an inventory-write
function or the apply-action service (design "Safety Boundary"). It performs no
LLM calls and mutates nothing.

The ``skus`` row does not expose ``classifications`` as a parsed list (it is
stored as a JSON array string) nor ``no_recent_sales`` as a bool (stored as an
integer flag), so :func:`sku_to_item` handles that mapping into the
:class:`~app.schemas.InventoryItem` API shape. That mapping helper is kept here
and is deliberately importable for reuse by the inventory router (task 7.2),
which needs the same row -> ``InventoryItem`` projection.
"""

from __future__ import annotations

import json
from typing import List

from sqlalchemy.orm import Session

from app.detection.classify import OVERSTOCK, STOCKOUT_RISK
from app.models import Sku
from app.repositories import recommendation_repo, sku_repo
from app.schemas import (
    CapitalTiedUp,
    CapitalTiedUpItem,
    DashboardResponse,
    ImpactResponse,
    InventoryItem,
    RecommendationStatus,
)


def _parse_classifications(raw: object) -> List[str]:
    """Parse the stored ``classifications`` JSON array into a list of labels.

    The column is a JSON array string (default ``"[]"``). Returns an empty list
    for anything missing or malformed so aggregation never raises on a bad row.
    Non-string entries are coerced to strings for a stable label set.
    """
    if not raw:
        return []
    if isinstance(raw, list):  # already-parsed (defensive)
        return [str(label) for label in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(label) for label in parsed]


def sku_to_item(row: Sku) -> InventoryItem:
    """Project a stored ``skus`` row onto the :class:`InventoryItem` API shape.

    Handles the two representation gaps between the ORM row and the API model:
    ``classifications`` is parsed from its JSON-array string into a ``list[str]``
    (Req 14.2), and ``no_recent_sales`` is converted from its integer flag
    (``1``/``0``) into a bool. Cached metric fields
    (``sales_velocity``/``days_of_cover``/``stockout_eta``) are passed through as
    stored, nullable for the zero-velocity edge (Req 2.5).

    Kept here (rather than inline in the dashboard build) so the inventory router
    (task 7.2) can reuse the identical mapping for ``GET /api/inventory`` and
    ``GET /api/inventory/{sku}``.
    """
    return InventoryItem(
        sku=row.sku,
        name=row.name,
        category=row.category,
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        unit_cost=row.unit_cost,
        selling_price=row.selling_price,
        supplier_name=row.supplier_name,
        avg_daily_sales=row.avg_daily_sales,
        last_sold_date=row.last_sold_date,
        sales_velocity=row.sales_velocity,
        days_of_cover=row.days_of_cover,
        stockout_eta=row.stockout_eta,
        no_recent_sales=bool(row.no_recent_sales),
        classifications=_parse_classifications(row.classifications),
        updated_at=row.updated_at,
    )


def _stockout_priority(item: InventoryItem) -> tuple:
    """Sort key ordering the stockout-risk group by urgency, soonest first.

    Primary key is ``days_of_cover`` ascending — the fewer days of stock left,
    the more urgent. A SKU whose ``days_of_cover`` is undefined (should not occur
    for a stockout-risk SKU, since the rule requires it defined, but handled
    defensively) sorts last via ``+inf``. Ties break on ``stockout_eta`` (earlier
    ISO date first, empty string sorts before any date) then ``sku`` id for a
    fully deterministic ordering.
    """
    doc = item.days_of_cover if item.days_of_cover is not None else float("inf")
    eta = item.stockout_eta or ""
    return (doc, eta, item.sku)


def _count_conditions(items: List[InventoryItem]) -> dict[str, int]:
    """Count, per condition label, how many SKUs carry that label.

    A SKU carrying several labels increments each of its labels' counts. Only
    labels actually present appear in the result. Shared by the Dashboard
    per-condition summary (Req 13.1) and the Impact aggregate condition metrics
    across all SKUs (Req 17.2).
    """
    condition_counts: dict[str, int] = {}
    for item in items:
        for label in item.classifications:
            condition_counts[label] = condition_counts.get(label, 0) + 1
    return condition_counts


def compute_capital_tied_up(items: List[InventoryItem]) -> CapitalTiedUp:
    """Translate overstock into the dollar capital it ties up ("cost of overstock").

    Overstock is not just a shelf-space problem: every unit held beyond what the
    reorder point calls for is capital sitting idle and incurring unnecessary
    storage costs. This turns that into a dollar figure.

    Accepts the already-projected :class:`InventoryItem` list (the same list
    :func:`build_dashboard` / :func:`build_impact` build from
    ``sku_repo.list_all``) and considers only SKUs whose ``classifications``
    include ``overstock``. For each such SKU the excess above the reorder point
    is ``max(0, current_stock - reorder_point)`` and the capital tied up is that
    excess valued at ``unit_cost``. Non-overstock SKUs never appear in ``by_sku``
    and never contribute to ``total``; both ``capital_tied_up`` and ``total`` are
    always >= 0.

    ``by_sku`` is ordered by ``capital_tied_up`` descending (tie-break ``sku``
    ascending) for a stable, deterministic output.

    Honest scope: the stored data has no historical snapshot table, so this
    reports the *current* capital tied up only — there is no fabricated
    before/after. The Impact page surfaces the delta contextually from this
    current value.

    Pure and read-only: reads the passed items and mutates nothing.
    """
    entries: List[CapitalTiedUpItem] = []
    for item in items:
        if OVERSTOCK not in item.classifications:
            continue
        excess_units = max(0, item.current_stock - item.reorder_point)
        capital_tied_up = float(excess_units * item.unit_cost)
        entries.append(
            CapitalTiedUpItem(
                sku=item.sku,
                excess_units=excess_units,
                capital_tied_up=capital_tied_up,
            )
        )

    # Descending by capital tied up, tie-break sku ascending, for stable output.
    entries.sort(key=lambda e: (-e.capital_tied_up, e.sku))

    total = float(sum(e.capital_tied_up for e in entries))
    return CapitalTiedUp(total=total, by_sku=entries)


def _count_lifecycle_statuses(session: Session) -> dict[str, int]:
    """Count recommendations per lifecycle status across the dataset.

    Every status (``pending``/``approved``/``rejected``/``applied``) is seeded at
    zero so the full lifecycle picture is always present, then actual rows are
    tallied through the ``recommendation_repo`` read path. Shared by the
    Dashboard per-lifecycle-status summary (Req 13.2) and the Impact outcome
    counts (Req 17.1).
    """
    counts: dict[str, int] = {status.value: 0 for status in RecommendationStatus}
    for rec in recommendation_repo.list_all(session):
        counts[rec.status] = counts.get(rec.status, 0) + 1
    return counts


def build_dashboard(session: Session) -> DashboardResponse:
    """Aggregate inventory + recommendation state for the Dashboard (Req 13).

    Reads every SKU and every recommendation through the repository read paths
    and produces the three Dashboard summaries:

    * ``condition_counts`` — number of SKUs carrying each detected condition
      label, counted from the parsed ``classifications`` of each SKU (Req 13.1).
      A SKU carrying several labels increments each of its labels' counts.
    * ``lifecycle_counts`` — number of recommendations in each lifecycle status,
      seeded so every status (``pending``/``approved``/``rejected``/``applied``)
      appears even at count zero (Req 13.2).
    * ``stockout_risk`` — the SKUs classified ``stockout_risk`` as a prioritised
      group, soonest-to-stock-out first (Req 13.3).

    Pure read-only aggregation: no inventory mutation, no LLM.
    """
    items = [sku_to_item(row) for row in sku_repo.list_all(session)]

    # Per-condition counts (Req 13.1): every label on every SKU contributes.
    condition_counts = _count_conditions(items)
    stockout_risk = [item for item in items if STOCKOUT_RISK in item.classifications]

    # Per-lifecycle-status counts (Req 13.2): seed all statuses at zero so the
    # dashboard shows a complete lifecycle picture, then tally actual rows.
    lifecycle_counts = _count_lifecycle_statuses(session)

    # Prioritised stockout-risk group (Req 13.3): soonest to stock out first.
    stockout_risk.sort(key=_stockout_priority)

    # Cost of overstock: capital tied up in excess stock (unnecessary storage).
    overstock_capital = compute_capital_tied_up(items)

    return DashboardResponse(
        condition_counts=condition_counts,
        lifecycle_counts=lifecycle_counts,
        stockout_risk=stockout_risk,
        overstock_capital_tied_up=overstock_capital,
    )


def build_impact(session: Session) -> ImpactResponse:
    """Aggregate recommendation outcomes + condition metrics for Impact (Req 17).

    Powers ``GET /api/impact`` (task 12.1) by reusing the Dashboard aggregation
    helpers so the two pages count the same way:

    * ``outcome_counts`` — recommendation counts by lifecycle status across the
      whole dataset, every status seeded at zero (Req 17.1). Same tally as the
      Dashboard's ``lifecycle_counts``.
    * ``condition_counts`` — aggregate per-condition SKU counts across all SKUs;
      each SKU contributes to every label it carries (Req 17.2). Same tally as
      the Dashboard's ``condition_counts``.
    * ``overstock_capital_tied_up`` — the *current* cost of overstock: capital
      sitting idle in excess stock across the dataset (total + per-SKU
      breakdown). Because the stored data has no historical snapshot table,
      this is an honest current-value figure only — no fabricated before/after;
      the frontend shows the delta contextually against this current value.

    Pure read-only aggregation: goes only through the ``sku_repo`` /
    ``recommendation_repo`` read paths, performs no LLM call, and mutates
    nothing (design "Safety Boundary").
    """
    items = [sku_to_item(row) for row in sku_repo.list_all(session)]

    return ImpactResponse(
        outcome_counts=_count_lifecycle_statuses(session),
        condition_counts=_count_conditions(items),
        overstock_capital_tied_up=compute_capital_tied_up(items),
    )
