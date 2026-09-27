"""Deterministic condition classification for STOCKY (Requirement 3).

Pure functions, no I/O, no LLM. Given a SKU's stored fields plus the metrics
already computed by ``detection/metrics.py`` (Sales_Velocity and Days_Of_Cover,
passed in as arguments), :func:`classify` attaches every applicable condition
label. Classification is fully deterministic: the same inputs always yield the
same label set (Req 3.7).

Sales_Velocity is now sourced directly from the CSV-provided ``avg_daily_sales``
field (see ``detection/metrics.py``), not derived from ``sales_history``.
``sales_history`` still flows into this module, but purely to feed the
trend/anomaly window-halves rule below (Req 3.6) — it plays no part in velocity
or any other rule here.

Thresholds are read from :mod:`app.config` so the rules stay configurable
without touching this module:

- ``FAST_MOVING_THRESHOLD``   — velocity above which a SKU is fast-moving (Req 3.3)
- ``SLOW_MOVING_THRESHOLD``   — velocity at/below which a SKU is slow-moving (Req 3.4)
- ``OVERSTOCK_DOC_THRESHOLD`` — days-of-cover above which a SKU is overstock (Req 3.5)
- ``ANOMALY_REL_THRESHOLD``   — relative change between window halves flagging a
  trend/anomaly (Req 3.6)

This module deliberately does NOT import ``detection/metrics.py``. Metric values
are supplied by the caller so the two modules stay decoupled and independently
testable.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, Sequence, runtime_checkable

from app import config

# Condition labels (kept as module constants so callers can reference them
# instead of hard-coding strings).
STOCKOUT_RISK = "stockout_risk"
NEEDS_REORDER = "needs_reorder"
FAST_MOVING = "fast_moving"
SLOW_MOVING = "slow_moving"
OVERSTOCK = "overstock"
TREND_ANOMALY = "trend_anomaly"

# Small positive constant to avoid divide-by-zero in the trend rule (Req 3.6).
_EPSILON = 1e-9


@runtime_checkable
class SkuLike(Protocol):
    """Structural type for the SKU data :func:`classify` reads.

    Any object exposing these attributes works — e.g. the ``SkuRow`` Pydantic
    model, a SQLAlchemy ``skus`` row, or a lightweight in-memory snapshot built
    by the Simulation_Engine. Only plain data is read; nothing is mutated.
    """

    current_stock: int
    reorder_point: int
    lead_time_days: int
    sales_history: Sequence[int]


def _mean(values: Sequence[float]) -> float:
    """Arithmetic mean of ``values``; 0.0 for an empty sequence."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def relative_change(sales_history: Sequence[int]) -> float:
    """Relative change in mean sales between the earlier and recent window halves.

    The window is split in two at ``W // 2``: the earlier half is
    ``history[: W // 2]`` and the recent half is ``history[W // 2 :]``. The
    result is ``(recent_mean - earlier_mean) / max(earlier_mean, epsilon)``; a
    positive value means demand rose, negative means it fell (Req 3.6).

    A window with fewer than two data points has no meaningful split and yields
    ``0.0`` (no anomaly).
    """
    history = list(sales_history)
    window = len(history)
    if window < 2:
        return 0.0
    half = window // 2
    earlier_mean = _mean(history[:half])
    recent_mean = _mean(history[half:])
    base = max(earlier_mean, _EPSILON)
    return (recent_mean - earlier_mean) / base


def is_trend_anomaly(
    sales_history: Sequence[int],
    threshold: Optional[float] = None,
) -> bool:
    """Return ``True`` when the window-halves relative change exceeds the threshold.

    Uses ``ANOMALY_REL_THRESHOLD`` from config unless ``threshold`` is given
    (Req 3.6). The comparison is on the absolute value so both surges and drops
    are flagged.
    """
    limit = config.ANOMALY_REL_THRESHOLD if threshold is None else threshold
    return abs(relative_change(sales_history)) > limit


def classify(
    sku: SkuLike,
    sales_velocity: float,
    days_of_cover: Optional[float],
) -> List[str]:
    """Attach every applicable condition label to a SKU (Req 3.7).

    Parameters
    ----------
    sku:
        SKU data exposing ``current_stock``, ``reorder_point``,
        ``lead_time_days``, and ``sales_history`` (see :class:`SkuLike`).
    sales_velocity:
        Units/day computed by ``detection/metrics.py`` (Req 2.1).
    days_of_cover:
        Days-of-cover computed by ``detection/metrics.py``, or ``None`` when
        Sales_Velocity is zero and it is undefined (Req 2.5). Rules that depend
        on days-of-cover only fire when it is defined.

    Returns
    -------
    list[str]
        The applicable labels, in a fixed deterministic order. Zero or more
        labels may apply; every rule that holds contributes its label (Req 3.7).

    Rules (design "Condition Rules"):
        - ``stockout_risk``  — days_of_cover defined and <= lead_time_days (Req 3.1)
        - ``needs_reorder``  — current_stock <= reorder_point               (Req 3.2)
        - ``fast_moving``    — sales_velocity > FAST_MOVING_THRESHOLD        (Req 3.3)
        - ``slow_moving``    — sales_velocity <= SLOW_MOVING_THRESHOLD       (Req 3.4)
        - ``overstock``      — days_of_cover defined and > OVERSTOCK_DOC_THRESHOLD (Req 3.5)
        - ``trend_anomaly``  — |window-halves relative change| > ANOMALY_REL_THRESHOLD (Req 3.6)
    """
    labels: List[str] = []

    # stockout_risk (Req 3.1) — only when days_of_cover is defined.
    if days_of_cover is not None and days_of_cover <= sku.lead_time_days:
        labels.append(STOCKOUT_RISK)

    # needs_reorder (Req 3.2).
    if sku.current_stock <= sku.reorder_point:
        labels.append(NEEDS_REORDER)

    # fast_moving (Req 3.3).
    if sales_velocity > config.FAST_MOVING_THRESHOLD:
        labels.append(FAST_MOVING)

    # slow_moving (Req 3.4).
    if sales_velocity <= config.SLOW_MOVING_THRESHOLD:
        labels.append(SLOW_MOVING)

    # overstock (Req 3.5) — only when days_of_cover is defined.
    if days_of_cover is not None and days_of_cover > config.OVERSTOCK_DOC_THRESHOLD:
        labels.append(OVERSTOCK)

    # trend_anomaly (Req 3.6).
    if is_trend_anomaly(sku.sales_history):
        labels.append(TREND_ANOMALY)

    return labels
