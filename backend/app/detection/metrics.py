"""Deterministic metric computation for the STOCKY Detection_Engine.

Pure functions only: no I/O, no database access, and no LLM invocation
(Requirement 2.4). ``Sales_Velocity`` is now read directly from the
CSV-provided ``avg_daily_sales`` field rather than derived from
``sales_history`` (per the schema migration: avg_daily_sales is the
authoritative velocity source). ``sales_history`` is no longer an input to this
module — it is used elsewhere (``detection/classify.py``) purely for the
trend/anomaly window-halves detection rule. Given a SKU's current stock and its
``avg_daily_sales``, these functions compute:

- ``Sales_Velocity`` = ``avg_daily_sales`` — Req 2.1.
- ``Days_Of_Cover`` = ``current_stock / Sales_Velocity`` when velocity > 0 — Req 2.2.
- ``Stockout_ETA`` = ``today + round(Days_Of_Cover)`` days as an ISO date string
  when velocity > 0 — Req 2.3.

When ``Sales_Velocity`` is zero or negative (treated as the zero-velocity
edge), ``Days_Of_Cover`` and ``Stockout_ETA`` are undefined (``None``) and the
SKU is marked as having no recent sales (Req 2.5).

The base date used for Stockout_ETA is injectable (``today`` parameter) so the
computation stays deterministic and testable; it defaults to the current date.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class Metrics:
    """The deterministic metrics computed for a single SKU snapshot.

    ``days_of_cover`` and ``stockout_eta`` are ``None`` when
    ``sales_velocity == 0`` (the zero-velocity edge, Req 2.5), in which case
    ``no_recent_sales`` is ``True``.

    ``stockout_eta`` is an ISO date string (``YYYY-MM-DD``) when defined.
    """

    sales_velocity: float
    days_of_cover: Optional[float] = None
    stockout_eta: Optional[str] = None
    no_recent_sales: bool = False


def compute_metrics(
    current_stock: int,
    avg_daily_sales: float,
    today: Optional[date] = None,
) -> Metrics:
    """Compute deterministic inventory metrics for a SKU.

    Args:
        current_stock: Units currently on hand.
        avg_daily_sales: The CSV-provided, authoritative Sales_Velocity
            (units/day). Any value <= 0 is treated as the zero-velocity edge.
        today: Base date for the Stockout_ETA projection. Defaults to
            ``date.today()`` when not supplied. Injectable to keep the
            computation deterministic and testable.

    Returns:
        A :class:`Metrics` value with ``sales_velocity`` always set. When
        velocity is greater than zero, ``days_of_cover`` and ``stockout_eta``
        are computed (Req 2.2, 2.3); when velocity is zero (or negative), both
        are ``None`` and ``no_recent_sales`` is ``True`` (Req 2.5).
    """
    # Sales_Velocity is read directly from avg_daily_sales (Req 2.1).
    sales_velocity = float(avg_daily_sales)

    if sales_velocity > 0:
        days_of_cover = current_stock / sales_velocity  # Req 2.2
        base = today if today is not None else date.today()
        stockout_eta = _add_days_iso(base, _round_days(days_of_cover))  # Req 2.3
        return Metrics(
            sales_velocity=sales_velocity,
            days_of_cover=days_of_cover,
            stockout_eta=stockout_eta,
            no_recent_sales=False,
        )

    # Zero velocity: Days_Of_Cover / Stockout_ETA undefined, flag no recent sales (Req 2.5).
    return Metrics(
        sales_velocity=sales_velocity,
        days_of_cover=None,
        stockout_eta=None,
        no_recent_sales=True,
    )


def _round_days(days_of_cover: float) -> int:
    """Round ``days_of_cover`` to an ``int`` day count, tolerating +/-``inf``.

    A vanishingly small (but positive) ``avg_daily_sales`` combined with a
    large-magnitude (possibly negative) ``current_stock`` can make
    ``current_stock / avg_daily_sales`` evaluate to +/-``float("inf")`` (or a
    value so large in magnitude that Python's built-in ``round`` cannot
    convert it to an ``int``, raising ``OverflowError``). Since
    ``_add_days_iso`` already clamps any day count beyond the representable
    ``date`` range to the nearest end of that range, an
    unrepresentable/overflowing round here is mapped to +/-``sys.maxsize`` — a
    large-but-finite sentinel of the same sign that ``_add_days_iso``
    recognises as "beyond any real calendar date" and clamps the same way.
    """
    if math.isinf(days_of_cover):
        return sys.maxsize if days_of_cover > 0 else -sys.maxsize
    try:
        return round(days_of_cover)
    except OverflowError:
        return sys.maxsize if days_of_cover > 0 else -sys.maxsize


def _add_days_iso(base: date, days: int) -> str:
    """Return ``base + days`` as an ISO date string (``YYYY-MM-DD``).

    Uses ``date.toordinal``/``fromordinal`` so the offset works for any
    day count (positive or negative) without relying on ``timedelta``
    overflow behaviour; ``days`` is a rounded day count from the caller.

    A vanishingly small ``avg_daily_sales`` (e.g. a denormal float) combined
    with an extreme ``current_stock`` can drive ``days_of_cover`` to an
    astronomically large-magnitude or infinite value in either direction,
    which ``date.fromordinal`` cannot represent. Such a projection is, for all
    practical purposes, "beyond any real calendar date" in that direction, so
    it is clamped to the nearest end of the representable ``date`` range
    rather than raising ``OverflowError``/``ValueError``.
    """
    min_days = date.min.toordinal() - base.toordinal()
    max_days = date.max.toordinal() - base.toordinal()
    if days != days:  # NaN guard (NaN != NaN)
        clamped = max_days
    else:
        clamped = min(max(days, min_days), max_days)
    return date.fromordinal(base.toordinal() + clamped).isoformat()
