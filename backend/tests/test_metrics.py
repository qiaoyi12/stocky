"""Property-based tests for the deterministic metric computation.

Covers **Property 2: Metric correctness including the zero-velocity edge**
from the STOCKY design's Correctness Properties.

Sales_Velocity is now read directly from the CSV-provided ``avg_daily_sales``
field (no longer derived from ``sales_history``). For any SKU:

- ``Sales_Velocity == avg_daily_sales`` (Req 2.1).
- When ``Sales_Velocity > 0``: ``Days_Of_Cover == current_stock / Sales_Velocity``
  (Req 2.2) and ``Stockout_ETA == base_date + round(Days_Of_Cover)`` days (Req 2.3).
- When ``Sales_Velocity <= 0`` (the zero-velocity edge, including a literal 0.0):
  ``Days_Of_Cover`` and ``Stockout_ETA`` are undefined and the SKU is marked as
  having no recent sales (Req 2.5).

**Validates: Requirements 2.1, 2.2, 2.3, 2.5**

Tests run from the ``backend`` directory so the ``app`` package is importable.
"""

from __future__ import annotations

import math
from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from app.detection.metrics import Metrics, compute_metrics

# A fixed base date keeps the Stockout_ETA projection deterministic across runs.
BASE_DATE = date(2024, 1, 1)

# Non-negative current stock (Req: units on hand cannot be negative).
current_stock_strategy = st.integers(min_value=0, max_value=1_000_000)

# avg_daily_sales: the authoritative velocity source. Non-negative floats,
# including 0.0 to reach the zero-velocity edge (Req 2.5).
avg_daily_sales_strategy = st.floats(
    min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False
)


def _expected_eta(base: date, days_of_cover: float) -> str:
    """Recompute Stockout_ETA independently via ordinal date arithmetic.

    A vanishingly small ``avg_daily_sales`` can make ``days_of_cover``
    infinite or astronomically large, which ``round``/``date.fromordinal``
    cannot represent. Mirror production's clamp to the maximum representable
    date in that case, rather than letting the oracle itself overflow.
    """
    min_days = date.min.toordinal() - base.toordinal()
    max_days = date.max.toordinal() - base.toordinal()
    if math.isinf(days_of_cover):
        days = max_days if days_of_cover > 0 else min_days
    elif days_of_cover > max_days:
        days = max_days
    elif days_of_cover < min_days:
        days = min_days
    else:
        days = round(days_of_cover)
    return date.fromordinal(base.toordinal() + days).isoformat()


@given(current_stock=current_stock_strategy, avg_daily_sales=avg_daily_sales_strategy)
def test_metric_correctness_property(current_stock: int, avg_daily_sales: float) -> None:
    """Property 2 across arbitrary stock and avg_daily_sales inputs.

    **Validates: Requirements 2.1, 2.2, 2.3, 2.5**
    """
    result = compute_metrics(current_stock, avg_daily_sales, today=BASE_DATE)

    # Req 2.1: Sales_Velocity == avg_daily_sales.
    assert math.isclose(
        result.sales_velocity, avg_daily_sales, rel_tol=1e-9, abs_tol=1e-12
    )

    if avg_daily_sales > 0:
        # Req 2.2: Days_Of_Cover == current_stock / Sales_Velocity.
        assert result.days_of_cover is not None
        expected_doc = current_stock / avg_daily_sales
        assert math.isclose(
            result.days_of_cover, expected_doc, rel_tol=1e-9, abs_tol=1e-12
        )

        # Req 2.3: Stockout_ETA == base_date + round(Days_Of_Cover) days.
        assert result.stockout_eta == _expected_eta(BASE_DATE, expected_doc)

        # Not the zero-velocity edge.
        assert result.no_recent_sales is False
    else:
        # Req 2.5: zero velocity -> Days_Of_Cover / Stockout_ETA undefined,
        # SKU marked as having no recent sales.
        assert result.days_of_cover is None
        assert result.stockout_eta is None
        assert result.no_recent_sales is True


@given(current_stock=current_stock_strategy)
def test_zero_avg_daily_sales_is_zero_velocity_edge(current_stock: int) -> None:
    """avg_daily_sales == 0 collapses to the zero-velocity / no-recent-sales edge (Req 2.5)."""
    result = compute_metrics(current_stock, 0.0, today=BASE_DATE)

    assert result.sales_velocity == 0.0
    assert result.days_of_cover is None
    assert result.stockout_eta is None
    assert result.no_recent_sales is True


def test_positive_velocity_example() -> None:
    """Concrete positive-velocity example anchoring Req 2.1, 2.2, 2.3."""
    result = compute_metrics(50, 5.0, today=BASE_DATE)

    assert result.sales_velocity == 5.0
    assert result.days_of_cover == 10.0  # 50 / 5.0
    assert result.stockout_eta == "2024-01-11"  # 2024-01-01 + 10 days
    assert result.no_recent_sales is False


def test_zero_velocity_example() -> None:
    """Concrete zero-velocity example anchoring Req 2.5."""
    result = compute_metrics(50, 0.0, today=BASE_DATE)

    assert result.sales_velocity == 0.0
    assert result.days_of_cover is None
    assert result.stockout_eta is None
    assert result.no_recent_sales is True
