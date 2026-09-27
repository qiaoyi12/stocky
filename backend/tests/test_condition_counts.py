"""Property 12 — Aggregate condition counts are consistent.

Exercises the Dashboard / Impact per-condition aggregation
(:func:`app.services.dashboard.build_dashboard` and
:func:`app.services.dashboard.build_impact`) end-to-end against a real
in-memory store. A varied set of SKUs is generated so that different
classification labels arise, loaded through the deterministic ingest loader
(which caches each SKU's ``classifications``), and then aggregated.

The property under test (design.md → Correctness Properties → Property 12):

    The aggregate per-condition counts surfaced on the Dashboard and Impact
    pages equal an independent recount computed directly from every stored
    SKU's classifications — the sum, over all SKUs, of each label the SKU
    carries. A SKU carrying several labels contributes to each of its labels'
    counts.

What the property asserts:

* ``build_dashboard(session).condition_counts`` equals an independently
  recomputed tally over each stored SKU's parsed ``classifications`` (Req 13.1).
* ``build_impact(session).condition_counts`` equals the same independent tally
  — the two pages count identically (Req 17.2).
* The grand total across all label counts equals the sum of
  ``len(classifications)`` over every stored SKU (no label is dropped or
  double-counted).
* Every SKU that appears in the prioritised ``stockout_risk`` group actually
  carries the ``stockout_risk`` label.

The independent recount is computed straight from the SKU rows via the
repository read path, deliberately NOT reusing the private ``_count_conditions``
helper the service uses, so the test is a genuine cross-check rather than a
tautology.

**Validates: Requirements 13.1, 17.2**
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from typing import Dict, Iterator, List

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.detection.classify import STOCKOUT_RISK
from app.ingest.loader import load_rows
from app.repositories import sku_repo
from app.schemas import SkuRow
from app.services.dashboard import build_dashboard, build_impact


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session``
    conftest fixture across generated examples, so each example builds its own
    isolated database inline — the same pattern used by the Property 9 test
    (``tests/test_apply_delta.py``).
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )
    from app import models  # noqa: F401  (register tables on Base.metadata)
    from app.db import Base

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


# --- Generators -------------------------------------------------------------


def _sku_row(index: int) -> st.SearchStrategy[SkuRow]:
    """Generate one SkuRow whose fields span the classification thresholds.

    The numeric ranges are chosen so that, across many draws, every condition
    label can arise:

    * ``current_stock`` vs ``reorder_point`` straddle to toggle
      ``needs_reorder`` (Req 3.2).
    * ``sales_history`` spans zero (drives ``slow_moving`` / ``no_recent_sales``)
      up through high daily rates (drives ``fast_moving`` when velocity exceeds
      the fast threshold) and includes uneven halves so ``trend_anomaly`` fires.
    * A short ``sales_history`` with a small stock and modest ``lead_time_days``
      lets ``days_of_cover`` fall to/under lead time (``stockout_risk``); a large
      stock with tiny sales pushes ``days_of_cover`` past the overstock
      threshold (``overstock``).

    The SKU id is derived from ``index`` so a generated set has unique ids
    (upsert would otherwise collapse duplicates).
    """

    def build(
        current_stock: int,
        reorder_point: int,
        lead_time_days: int,
        unit_cost: float,
        sales_history: List[int],
    ) -> SkuRow:
        # avg_daily_sales is now the authoritative velocity source (no longer
        # derived from sales_history), but deriving it as the history mean here
        # preserves the original test's coverage of every velocity regime
        # (zero/slow/fast) driven by the generated sales_history shape.
        avg_daily_sales = (
            sum(sales_history) / len(sales_history) if sales_history else 0.0
        )
        return SkuRow(
            sku=f"SKU-{index:04d}",
            name=f"Widget {index}",
            category="general",
            current_stock=current_stock,
            reorder_point=reorder_point,
            lead_time_days=lead_time_days,
            unit_cost=unit_cost,
            selling_price=unit_cost + 1.0,
            supplier_name="Supplier A",
            avg_daily_sales=avg_daily_sales,
            sales_history=sales_history,
            last_sold_date="2024-01-01",
        )

    return st.builds(
        build,
        current_stock=st.integers(min_value=0, max_value=5_000),
        reorder_point=st.integers(min_value=0, max_value=500),
        lead_time_days=st.integers(min_value=1, max_value=30),
        unit_cost=st.floats(
            min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False
        ),
        # Length 0..14, values 0..60/day: spans zero-velocity, slow, and fast
        # regimes plus uneven halves for the trend rule.
        sales_history=st.lists(
            st.integers(min_value=0, max_value=60), min_size=0, max_size=14
        ),
    )


def _sku_set() -> st.SearchStrategy[List[SkuRow]]:
    """Generate a set of 1..8 SkuRows with distinct ids and varied fields."""
    return st.integers(min_value=1, max_value=8).flatmap(
        lambda n: st.tuples(*[_sku_row(i) for i in range(n)]).map(list)
    )


def _independent_condition_counts(session: Session) -> Dict[str, int]:
    """Recount per-condition SKU counts straight from stored rows.

    Reads each SKU's ``classifications`` JSON directly (not via the service's
    private helper) and tallies, so this is a genuine independent cross-check of
    the aggregation. A SKU carrying several labels contributes to each label.
    """
    counts: Dict[str, int] = {}
    for row in sku_repo.list_all(session):
        raw = row.classifications
        labels = json.loads(raw) if raw else []
        for label in labels:
            counts[label] = counts.get(label, 0) + 1
    return counts


def _total_labels(session: Session) -> int:
    """Sum of ``len(classifications)`` over every stored SKU."""
    total = 0
    for row in sku_repo.list_all(session):
        raw = row.classifications
        labels = json.loads(raw) if raw else []
        total += len(labels)
    return total


# --- Property 12 ------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(rows=_sku_set())
def test_aggregate_condition_counts_are_consistent(rows: List[SkuRow]):
    """Dashboard/Impact condition counts equal an independent recount.

    For any generated set of SKUs loaded through the deterministic ingest
    loader:

    - ``build_dashboard`` and ``build_impact`` report the SAME
      ``condition_counts``;
    - those counts equal an independent per-label tally computed directly from
      each stored SKU's classifications;
    - the grand total across labels equals the sum of ``len(classifications)``
      over all SKUs;
    - every SKU in the ``stockout_risk`` group carries the ``stockout_risk``
      label.

    **Validates: Requirements 13.1, 17.2**
    """
    with _temp_session() as session:
        load_rows(session, rows)

        expected = _independent_condition_counts(session)

        dashboard = build_dashboard(session)
        impact = build_impact(session)

        # Dashboard and Impact count conditions identically (Req 13.1, 17.2).
        assert dashboard.condition_counts == expected
        assert impact.condition_counts == expected

        # No label dropped or double-counted: grand total == sum of label counts.
        assert sum(dashboard.condition_counts.values()) == _total_labels(session)

        # Every SKU surfaced as stockout-risk actually carries that label.
        for item in dashboard.stockout_risk:
            assert STOCKOUT_RISK in item.classifications


# --- Focused examples using the shared conftest `session` fixture -----------


def test_multi_label_sku_contributes_to_each_count(session):
    """A SKU carrying several labels increments each label's count once.

    A low-stock, zero-sales SKU is both ``needs_reorder`` (stock <= reorder) and
    ``slow_moving`` (velocity 0 <= slow threshold), so both labels count it.

    **Validates: Requirements 13.1, 17.2**
    """
    rows = [
        SkuRow(
            sku="SKU-MULTI",
            name="Multi",
            category="general",
            current_stock=1,
            reorder_point=50,
            lead_time_days=7,
            unit_cost=1.0,
            selling_price=2.0,
            supplier_name="Supplier A",
            avg_daily_sales=0.0,
            sales_history=[0, 0, 0, 0],
            last_sold_date="2024-01-01",
        ),
    ]
    load_rows(session, rows)

    dashboard = build_dashboard(session)
    impact = build_impact(session)

    stored = sku_repo.get(session, "SKU-MULTI")
    labels = json.loads(stored.classifications)
    assert "needs_reorder" in labels
    assert "slow_moving" in labels

    for label in labels:
        assert dashboard.condition_counts[label] == 1
        assert impact.condition_counts[label] == 1
    assert dashboard.condition_counts == impact.condition_counts


def test_empty_store_has_empty_condition_counts(session):
    """With no SKUs loaded, both aggregations report empty condition counts.

    **Validates: Requirements 13.1, 17.2**
    """
    dashboard = build_dashboard(session)
    impact = build_impact(session)

    assert dashboard.condition_counts == {}
    assert impact.condition_counts == {}
    assert dashboard.stockout_risk == []
