"""Property tests for deterministic condition classification (Task 3.4).

Property 3: Deterministic, complete condition classification.

For any SKU, classifying it twice yields the identical label set, and that set
contains exactly the labels whose rules hold:

- ``stockout_risk``  iff Days_Of_Cover is defined and <= lead_time_days
- ``needs_reorder``  iff current_stock <= reorder_point
- ``fast_moving``    iff velocity > FAST_MOVING_THRESHOLD
- ``slow_moving``    iff velocity <= SLOW_MOVING_THRESHOLD
- ``overstock``      iff Days_Of_Cover is defined and > OVERSTOCK_DOC_THRESHOLD
- ``trend_anomaly``  iff |relative change between window halves| > ANOMALY_REL_THRESHOLD

Each rule is recomputed independently here (against the same ``app.config``
thresholds the implementation reads) so the test verifies both soundness (no
label present whose rule is false) and completeness (no label missing whose
rule is true), not merely that the code agrees with itself.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from hypothesis import given, settings
from hypothesis import strategies as st

# Make the backend package importable regardless of pytest's rootdir.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app import config  # noqa: E402
from app.detection import classify as classify_mod  # noqa: E402
from app.detection.classify import (  # noqa: E402
    FAST_MOVING,
    NEEDS_REORDER,
    OVERSTOCK,
    SLOW_MOVING,
    STOCKOUT_RISK,
    TREND_ANOMALY,
    classify,
    relative_change,
)


@dataclass
class FakeSku:
    """Minimal SKU snapshot exposing the fields ``classify`` reads."""

    current_stock: int
    reorder_point: int
    lead_time_days: int
    sales_history: List[int]


# --- Strategies -------------------------------------------------------------

# Non-negative counts / days; bounds kept modest so shrinking is fast.
_counts = st.integers(min_value=0, max_value=10_000)
_lead_time = st.integers(min_value=0, max_value=365)

# Sales history: 0..30 non-negative integers. Include lengths 0 and 1 so the
# "window < 2 => no anomaly" branch of relative_change is exercised, and larger
# windows (with a mix of low and high values) so anomalies actually trigger.
_sales_history = st.lists(st.integers(min_value=0, max_value=500), min_size=0, max_size=30)

# Sales velocity: non-negative float, no NaN/inf. Range straddles both the slow
# (1.0) and fast (20.0) thresholds so all velocity branches are hit.
_velocity = st.floats(
    min_value=0.0,
    max_value=1000.0,
    allow_nan=False,
    allow_infinity=False,
)

# Days-of-cover: either undefined (None) or a non-negative float. Range straddles
# the overstock threshold (90.0) and small values near lead-time bounds.
_days_of_cover = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)


def _expected_labels(
    sku: FakeSku,
    velocity: float,
    doc: Optional[float],
) -> set:
    """Recompute the expected label set directly from the rules.

    Uses ``app.config`` thresholds and ``classify.relative_change`` for the
    trend rule, independent of ``classify``'s own branching.
    """
    expected: set = set()

    if doc is not None and doc <= sku.lead_time_days:
        expected.add(STOCKOUT_RISK)
    if sku.current_stock <= sku.reorder_point:
        expected.add(NEEDS_REORDER)
    if velocity > config.FAST_MOVING_THRESHOLD:
        expected.add(FAST_MOVING)
    if velocity <= config.SLOW_MOVING_THRESHOLD:
        expected.add(SLOW_MOVING)
    if doc is not None and doc > config.OVERSTOCK_DOC_THRESHOLD:
        expected.add(OVERSTOCK)
    if abs(relative_change(sku.sales_history)) > config.ANOMALY_REL_THRESHOLD:
        expected.add(TREND_ANOMALY)

    return expected


@settings(max_examples=200)
@given(
    current_stock=_counts,
    reorder_point=_counts,
    lead_time_days=_lead_time,
    sales_history=_sales_history,
    velocity=_velocity,
    days_of_cover=_days_of_cover,
)
def test_classification_deterministic_and_complete(
    current_stock: int,
    reorder_point: int,
    lead_time_days: int,
    sales_history: List[int],
    velocity: float,
    days_of_cover: Optional[float],
) -> None:
    sku = FakeSku(
        current_stock=current_stock,
        reorder_point=reorder_point,
        lead_time_days=lead_time_days,
        sales_history=list(sales_history),
    )

    first = classify(sku, velocity, days_of_cover)
    second = classify(sku, velocity, days_of_cover)

    # Determinism (Req 3.7): identical inputs -> identical label set.
    assert first == second

    # No duplicate labels are emitted.
    assert len(first) == len(set(first))

    # Soundness + completeness (Req 3.1-3.6): the set is exactly the labels
    # whose rule holds, recomputed independently.
    assert set(first) == _expected_labels(sku, velocity, days_of_cover)


def test_no_days_of_cover_suppresses_doc_rules() -> None:
    """When Days_Of_Cover is undefined, neither stockout_risk nor overstock fires."""
    sku = FakeSku(current_stock=100, reorder_point=0, lead_time_days=30, sales_history=[])
    labels = classify(sku, sales_velocity=0.0, days_of_cover=None)
    assert STOCKOUT_RISK not in labels
    assert OVERSTOCK not in labels


def test_slow_and_stockout_boundaries_are_inclusive() -> None:
    """velocity == slow threshold is slow; doc == lead_time is stockout risk."""
    sku = FakeSku(current_stock=0, reorder_point=0, lead_time_days=5, sales_history=[])
    labels = classify(sku, sales_velocity=config.SLOW_MOVING_THRESHOLD, days_of_cover=5.0)
    assert SLOW_MOVING in labels
    assert STOCKOUT_RISK in labels


def test_trend_anomaly_flags_a_clear_surge() -> None:
    """A clear jump between window halves exceeds the anomaly threshold."""
    sku = FakeSku(current_stock=50, reorder_point=10, lead_time_days=7, sales_history=[1, 1, 100, 100])
    assert classify_mod.is_trend_anomaly(sku.sales_history)
    labels = classify(sku, sales_velocity=5.0, days_of_cover=10.0)
    assert TREND_ANOMALY in labels
