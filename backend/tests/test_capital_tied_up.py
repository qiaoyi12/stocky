"""Property — Cost of overstock (capital tied up) invariants.

Exercises :func:`app.services.dashboard.compute_capital_tied_up`, which
translates overstock into the dollar capital it ties up. The function takes an
already-projected :class:`~app.schemas.InventoryItem` list directly, so these
tests build ``InventoryItem`` instances with hand-driven ``classifications``
(no DB / session needed) — following the label-driven style of
``tests/test_condition_counts.py``.

The properties under test:

* Every entry in ``by_sku`` corresponds to a SKU whose ``classifications``
  include ``overstock`` — capital is never computed for a non-overstock SKU.
* Every ``capital_tied_up`` and every ``excess_units`` is >= 0.
* ``total`` equals the sum of ``by_sku`` ``capital_tied_up`` (within float
  tolerance) and ``total`` >= 0.
* No non-overstock SKU ever appears in ``by_sku``.

**Validates: Requirements 3.5**
"""

from __future__ import annotations

from typing import List

from hypothesis import given, settings
from hypothesis import strategies as st

from app.detection.classify import (
    FAST_MOVING,
    NEEDS_REORDER,
    OVERSTOCK,
    SLOW_MOVING,
    STOCKOUT_RISK,
    TREND_ANOMALY,
)
from app.schemas import InventoryItem
from app.services.dashboard import compute_capital_tied_up

# Labels other than "overstock", drawn to build varied classification sets that
# may or may not include the overstock label.
_OTHER_LABELS = [
    STOCKOUT_RISK,
    NEEDS_REORDER,
    FAST_MOVING,
    SLOW_MOVING,
    TREND_ANOMALY,
]


# --- Generators -------------------------------------------------------------


def _inventory_item(index: int) -> st.SearchStrategy[InventoryItem]:
    """Generate one InventoryItem with varied stock/reorder/cost and labels.

    ``current_stock`` and ``reorder_point`` straddle each other (including
    ``current_stock < reorder_point``, which yields zero excess) so the
    ``max(0, ...)`` clamp is exercised. ``classifications`` independently may or
    may not include ``overstock`` alongside any mix of other labels, so the
    filter is tested on both overstock and non-overstock SKUs.
    """

    def build(
        current_stock: int,
        reorder_point: int,
        unit_cost: float,
        include_overstock: bool,
        other_labels: List[str],
    ) -> InventoryItem:
        classifications = list(other_labels)
        if include_overstock:
            classifications.append(OVERSTOCK)
        return InventoryItem(
            sku=f"SKU-{index:04d}",
            name=f"Widget {index}",
            category="general",
            current_stock=current_stock,
            reorder_point=reorder_point,
            lead_time_days=7,
            unit_cost=unit_cost,
            selling_price=unit_cost + 1.0,
            supplier_name="Supplier A",
            avg_daily_sales=1.0,
            classifications=classifications,
        )

    return st.builds(
        build,
        current_stock=st.integers(min_value=0, max_value=5_000),
        reorder_point=st.integers(min_value=0, max_value=500),
        unit_cost=st.floats(
            min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
        include_overstock=st.booleans(),
        other_labels=st.lists(
            st.sampled_from(_OTHER_LABELS), min_size=0, max_size=len(_OTHER_LABELS), unique=True
        ),
    )


def _item_set() -> st.SearchStrategy[List[InventoryItem]]:
    """Generate a set of 0..8 InventoryItems with distinct ids."""
    return st.integers(min_value=0, max_value=8).flatmap(
        lambda n: st.tuples(*[_inventory_item(i) for i in range(n)]).map(list)
    )


# --- Property ---------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(items=_item_set())
def test_capital_tied_up_invariants(items: List[InventoryItem]):
    """Capital tied up is computed only for overstock SKUs and is consistent.

    **Validates: Requirements 3.5**
    """
    result = compute_capital_tied_up(items)

    overstock_skus = {i.sku for i in items if OVERSTOCK in i.classifications}
    non_overstock_skus = {i.sku for i in items if OVERSTOCK not in i.classifications}

    # Every by_sku entry belongs to an overstock SKU; none belong to a
    # non-overstock SKU.
    for entry in result.by_sku:
        assert entry.sku in overstock_skus
        assert entry.sku not in non_overstock_skus
        # Non-negative units and capital.
        assert entry.excess_units >= 0
        assert entry.capital_tied_up >= 0.0

    # No non-overstock sku appears in by_sku.
    by_sku_ids = {entry.sku for entry in result.by_sku}
    assert by_sku_ids.isdisjoint(non_overstock_skus)

    # total == sum of by_sku capital, within float tolerance, and total >= 0.
    expected_total = sum(entry.capital_tied_up for entry in result.by_sku)
    assert abs(result.total - expected_total) <= 1e-6
    assert result.total >= 0.0


# --- Focused examples -------------------------------------------------------


def test_excess_clamped_to_zero_when_stock_below_reorder():
    """An overstock SKU with stock below its reorder point yields zero excess.

    **Validates: Requirements 3.5**
    """
    items = [
        InventoryItem(
            sku="SKU-LOW",
            name="Low",
            category="general",
            current_stock=10,
            reorder_point=50,
            lead_time_days=7,
            unit_cost=4.0,
            selling_price=5.0,
            supplier_name="Supplier A",
            avg_daily_sales=1.0,
            classifications=[OVERSTOCK],
        )
    ]
    result = compute_capital_tied_up(items)
    assert result.total == 0.0
    assert result.by_sku[0].excess_units == 0
    assert result.by_sku[0].capital_tied_up == 0.0


def test_non_overstock_sku_excluded():
    """A SKU without the overstock label is excluded from total and by_sku.

    **Validates: Requirements 3.5**
    """
    items = [
        InventoryItem(
            sku="SKU-A",
            name="A",
            category="general",
            current_stock=500,
            reorder_point=100,
            lead_time_days=7,
            unit_cost=2.0,
            selling_price=3.0,
            supplier_name="Supplier A",
            avg_daily_sales=1.0,
            classifications=[OVERSTOCK],
        ),
        InventoryItem(
            sku="SKU-B",
            name="B",
            category="general",
            current_stock=900,
            reorder_point=100,
            lead_time_days=7,
            unit_cost=5.0,
            selling_price=6.0,
            supplier_name="Supplier B",
            avg_daily_sales=25.0,
            classifications=[FAST_MOVING],  # not overstock
        ),
    ]
    result = compute_capital_tied_up(items)
    skus = {e.sku for e in result.by_sku}
    assert "SKU-B" not in skus
    assert skus == {"SKU-A"}
    # (500-100)*2.0 = 800.0
    assert result.total == 800.0


def test_by_sku_ordered_by_capital_descending():
    """by_sku is ordered by capital_tied_up descending, tie-break sku ascending.

    **Validates: Requirements 3.5**
    """
    items = [
        InventoryItem(
            sku="SKU-SMALL",
            name="small",
            category="general",
            current_stock=110,
            reorder_point=100,
            lead_time_days=7,
            unit_cost=1.0,  # excess 10 -> 10.0
            selling_price=2.0,
            supplier_name="Supplier A",
            avg_daily_sales=1.0,
            classifications=[OVERSTOCK],
        ),
        InventoryItem(
            sku="SKU-BIG",
            name="big",
            category="general",
            current_stock=300,
            reorder_point=100,
            lead_time_days=7,
            unit_cost=5.0,  # excess 200 -> 1000.0
            selling_price=6.0,
            supplier_name="Supplier B",
            avg_daily_sales=1.0,
            classifications=[OVERSTOCK],
        ),
    ]
    result = compute_capital_tied_up(items)
    assert [e.sku for e in result.by_sku] == ["SKU-BIG", "SKU-SMALL"]
    assert result.total == 1010.0
