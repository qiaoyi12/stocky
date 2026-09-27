"""SKU / inventory repository — the single-writer split.

This module is the centrepiece of STOCKY's safety boundary. It is deliberately
split into two clearly separated sections:

* ``READ FUNCTIONS`` — pure lookups used freely across the app (agents receive
  only plain-data snapshots derived from these, never the session).
* ``INVENTORY WRITE FUNCTIONS`` — the ONLY functions in the whole system that
  mutate ``skus`` / ``sales_history`` rows.

Everything that changes inventory state flows through the write section:
deterministic ingest at upload time, and the deterministic Apply-Action Service
after Reviewer approval (Req 9.3, 11.1). No agent module and no orchestrator
module imports the write functions — that isolation is what the safety-boundary
import-graph test asserts (design "The Safety Boundary").

The write section is grouped under an explicit banner and every write function
name carries an unambiguous verb (``upsert_``/``insert_``/``apply_``/``set_``)
so the import-graph check and human reviewers can identify the mutating surface
at a glance.

Uploading a new CSV replaces the current inventory outright — there is no
dataset scoping. Every function operates on the single, current set of
``skus`` / ``sales_history`` rows.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SalesHistory, Sku


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string (used for ``updated_at``)."""
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# READ FUNCTIONS
# ---------------------------------------------------------------------------
# Pure lookups. Safe to import anywhere, including read-only services and the
# simulation engine. These never mutate inventory state.
# ===========================================================================


def get(session: Session, sku: str) -> Optional[Sku]:
    """Return the SKU row for ``sku``, or ``None`` if it does not exist."""
    return session.get(Sku, sku)


def list_all(session: Session) -> List[Sku]:
    """Return every SKU row, ordered by SKU id for deterministic output."""
    return list(session.scalars(select(Sku).order_by(Sku.sku)))


def exists(session: Session, sku: str) -> bool:
    """Return ``True`` when a SKU row exists for ``sku``."""
    return session.get(Sku, sku) is not None


def get_sales_history(session: Session, sku: str) -> List[SalesHistory]:
    """Return the sales-history rows for ``sku`` ordered by day (oldest first)."""
    return list(
        session.scalars(
            select(SalesHistory).where(SalesHistory.sku == sku).order_by(SalesHistory.day)
        )
    )


def get_sales_units(session: Session, sku: str) -> List[int]:
    """Return just the daily unit counts for ``sku`` (oldest first).

    Convenience for the deterministic metric functions, which take a plain list
    of recent daily sales.
    """
    return [row.units for row in get_sales_history(session, sku)]


# ===========================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  INVENTORY WRITE FUNCTIONS — THE ONLY INVENTORY MUTATORS                  │
# ├─────────────────────────────────────────────────────────────────────────┤
# │  Everything below this banner writes ``skus`` / ``sales_history``.        │
# │  ONLY the deterministic ingest path and the post-approval Apply-Action    │
# │  Service (services/apply_action.py) may import these functions.           │
# │  Agent and orchestrator modules MUST NOT import anything below this line  │
# │  (enforced by the safety-boundary import-graph test — Req 9.3, 11.1).     │
# └─────────────────────────────────────────────────────────────────────────┘
# ===========================================================================


def upsert_sku(
    session: Session,
    *,
    sku: str,
    name: str,
    category: str,
    current_stock: int,
    reorder_point: int,
    lead_time_days: int,
    unit_cost: float,
    selling_price: float,
    supplier_name: str,
    avg_daily_sales: float,
    last_sold_date: Optional[str] = None,
) -> Sku:
    """Create or update the core fields of a SKU row (deterministic ingest).

    Cached metric columns (``sales_velocity``, ``days_of_cover`` …) are left for
    :func:`set_metrics` to populate; this writes only the source-of-truth fields
    parsed from the uploaded CSV. ``avg_daily_sales`` is the authoritative
    Sales_Velocity source; ``sales_history`` (written separately via
    :func:`replace_sales_history`) now feeds only trend/anomaly detection.
    """
    row = session.get(Sku, sku)
    if row is None:
        row = Sku(sku=sku)
        session.add(row)
    row.name = name
    row.category = category
    row.current_stock = current_stock
    row.reorder_point = reorder_point
    row.lead_time_days = lead_time_days
    row.unit_cost = unit_cost
    row.selling_price = selling_price
    row.supplier_name = supplier_name
    row.avg_daily_sales = avg_daily_sales
    row.last_sold_date = last_sold_date
    row.updated_at = _now_iso()
    session.flush()
    return row


def replace_sales_history(
    session: Session,
    *,
    sku: str,
    units: Sequence[int],
    base_date: Optional[date] = None,
) -> List[SalesHistory]:
    """Replace all sales-history rows for ``sku`` with ``units`` (deterministic ingest).

    ``units`` is the recent daily sales series, oldest first. Days are assigned
    consecutive ISO dates ending today (``base_date`` overridable for tests) so
    the ``(sku, day)`` uniqueness constraint holds. Any existing rows for the
    SKU are removed first, making re-ingest idempotent.
    """
    for existing in get_sales_history(session, sku):
        session.delete(existing)
    session.flush()

    end = base_date or datetime.now(timezone.utc).date()
    window = len(units)
    rows: List[SalesHistory] = []
    for offset, unit_count in enumerate(units):
        # oldest entry is (window-1) days back; newest is base_date.
        day = end - timedelta(days=(window - 1 - offset))
        row = SalesHistory(sku=sku, day=day.isoformat(), units=int(unit_count))
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def set_metrics(
    session: Session,
    *,
    sku: str,
    sales_velocity: Optional[float],
    days_of_cover: Optional[float],
    stockout_eta: Optional[str],
    no_recent_sales: bool,
    classifications: Iterable[str],
) -> Sku:
    """Cache recomputed deterministic metrics + classifications on the SKU row.

    Called by the deterministic detection step after ingest (and on chaos
    re-run). ``classifications`` is stored as a JSON array of label strings.
    Raises ``KeyError`` if the SKU does not exist.
    """
    row = session.get(Sku, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")
    row.sales_velocity = sales_velocity
    row.days_of_cover = days_of_cover
    row.stockout_eta = stockout_eta
    row.no_recent_sales = 1 if no_recent_sales else 0
    row.classifications = json.dumps(list(classifications))
    row.updated_at = _now_iso()
    session.flush()
    return row


def apply_reorder(session: Session, *, sku: str, quantity: int) -> Sku:
    """Increase ``current_stock`` by ``quantity`` (REORDER action).

    Deterministic apply-action delta for a ``reorder`` recommendation. Affects
    only the target SKU. Raises ``KeyError`` if the SKU does not exist.
    """
    row = session.get(Sku, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")
    row.current_stock = row.current_stock + int(quantity)
    row.updated_at = _now_iso()
    session.flush()
    return row


def apply_adjust_reorder(session: Session, *, sku: str, new_reorder_point: int) -> Sku:
    """Set ``reorder_point`` to ``new_reorder_point`` (ADJUST_REORDER action).

    Deterministic apply-action delta for an ``adjust_reorder`` recommendation.
    Affects only the target SKU. Raises ``KeyError`` if the SKU does not exist.
    """
    row = session.get(Sku, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")
    row.reorder_point = int(new_reorder_point)
    row.updated_at = _now_iso()
    session.flush()
    return row
