"""Property 15 — Chaos affects only the working copy.

Exercises :func:`app.services.chaos.apply_chaos` end-to-end against a real
in-memory store. A set of SKUs is seeded through the deterministic ingest loader
(with a fixed ``base_date`` so cached ``stockout_eta`` values are deterministic),
then a random Chaos scenario + intensity is injected — sometimes against an
explicit subset of SKUs, sometimes against the whole dataset — and the outcome
is checked against Property 15.

The property under test (design.md -> Correctness Properties -> Property 15):

    Chaos_Mode injects a synthetic disruptive event onto an in-memory working
    copy and reruns the Detection_Engine classification on the affected SKUs
    (Req 18.1, 18.2), but NEVER alters the originally uploaded source data: the
    persisted ``skus`` / ``sales_history`` rows are byte-for-byte unchanged after
    ``apply_chaos`` runs (Req 18.3).

Facets asserted:

* **Working-copy isolation (Req 18.3)** — a full snapshot of the ``skus`` and
  ``sales_history`` tables (every column) is captured before and after
  ``apply_chaos`` and asserted unchanged; ``response.source_unchanged`` is True.
* **Correct targeting (Req 18.1)** — ``affected_count`` equals the number of
  targeted SKUs (the explicit subset when given, else every SKU), and there is
  one result per targeted SKU.
* **Reclassification on the working copy (Req 18.2)** — each result carries a
  recomputed before/after metric set and valid label lists, and the perturbation
  moves the working-copy input in the documented direction (``demand_spike``
  raises after-velocity, ``supply_delay`` raises after-lead-time), while the
  *source* row is unchanged.
* **Unknown SKU** — an explicitly requested unknown SKU raises ``KeyError`` and
  leaves the source data untouched.

**Validates: Requirements 18.1, 18.2, 18.3**
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from typing import Iterator, List, Sequence, Set

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.detection.classify import (
    FAST_MOVING,
    NEEDS_REORDER,
    OVERSTOCK,
    SLOW_MOVING,
    STOCKOUT_RISK,
    TREND_ANOMALY,
)
from app.ingest.loader import load_rows
from app.models import SalesHistory, Sku
from app.schemas import SkuRow
from app.services.chaos import ChaosRequest, apply_chaos

# Fixed anchor date so ingest-cached stockout ETAs are deterministic across
# generated examples.
BASE_DATE = date(2024, 1, 15)

# The complete, closed set of condition labels the Detection_Engine can emit.
# Every ``classifications_before/after`` list must be a subset of this set.
_ALL_LABELS: Set[str] = {
    STOCKOUT_RISK,
    NEEDS_REORDER,
    FAST_MOVING,
    SLOW_MOVING,
    OVERSTOCK,
    TREND_ANOMALY,
}


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session`` conftest
    fixture across generated examples, so each example builds its own isolated
    database inline — the same pattern used by the Property 9 / Property 14 tests.
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


def _snapshot_tables(session: Session) -> tuple:
    """Capture every ``skus`` and ``sales_history`` column as a comparable tuple.

    Returns a nested tuple ``(sku_rows, sales_rows)`` ordered deterministically,
    so equality before vs after ``apply_chaos`` proves the persisted source store
    was not mutated (Req 18.3).
    """
    sku_rows = tuple(
        (
            r.sku,
            r.name,
            r.category,
            r.current_stock,
            r.reorder_point,
            r.lead_time_days,
            r.unit_cost,
            r.sales_velocity,
            r.days_of_cover,
            r.stockout_eta,
            r.no_recent_sales,
            r.classifications,
            r.updated_at,
        )
        for r in session.scalars(select(Sku).order_by(Sku.sku))
    )
    sales_rows = tuple(
        (r.id, r.sku, r.day, r.units)
        for r in session.scalars(
            select(SalesHistory).order_by(SalesHistory.sku, SalesHistory.day, SalesHistory.id)
        )
    )
    return (sku_rows, sales_rows)


def _seed(session: Session, rows: Sequence[SkuRow]) -> None:
    """Seed the store with ``rows`` via the deterministic ingest loader.

    A fixed ``base_date`` keeps the cached metrics (including ``stockout_eta``)
    deterministic.
    """
    load_rows(session, list(rows), base_date=BASE_DATE)


# --- Generators -------------------------------------------------------------

# Daily sales list; short and bounded, with a positive floor on some entries so
# demand-spike scaling is observable while still reaching the zero-velocity edge.
_sales_history = st.lists(st.integers(min_value=0, max_value=50), min_size=0, max_size=8)


def _sku_row(sku: str) -> st.SearchStrategy[SkuRow]:
    """Generate a plausible :class:`SkuRow` for ``sku``."""

    def build(stock, reorder, lead, cost, hist):
        # avg_daily_sales derived as the history mean so demand_spike/drop
        # perturbations (which scale avg_daily_sales) still exercise the same
        # velocity regimes the original history-driven generator covered.
        avg_daily_sales = sum(hist) / len(hist) if hist else 0.0
        return SkuRow(
            sku=sku,
            name=f"Widget {sku}",
            category="general",
            current_stock=stock,
            reorder_point=reorder,
            lead_time_days=lead,
            unit_cost=cost,
            selling_price=cost + 1.0,
            supplier_name="Supplier A",
            avg_daily_sales=avg_daily_sales,
            sales_history=hist,
            last_sold_date="2024-01-01",
        )

    return st.builds(
        build,
        stock=st.integers(min_value=0, max_value=10_000),
        reorder=st.integers(min_value=0, max_value=10_000),
        lead=st.integers(min_value=1, max_value=60),
        cost=st.floats(
            min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False
        ),
        hist=_sales_history,
    )


def _chaos_case() -> st.SearchStrategy[dict]:
    """Generate a set of seed SKUs plus a Chaos scenario, intensity, and target set.

    ``intensity`` is drawn from ``[1.0, 10.0]`` so the documented monotonic
    direction of each scenario holds deterministically (scaling a non-negative
    integer by ``>= 1`` and rounding never decreases it). ``targets`` is either
    ``None`` (affect every SKU) or an explicit non-empty subset of the seeded
    SKU ids, exercising both targeting paths.
    """

    def build(
        n: int, scenario: str, intensity: float, target_choice: float
    ) -> st.SearchStrategy[dict]:
        sku_ids = [f"SKU-{i}" for i in range(n)]
        row_strats = [_sku_row(s) for s in sku_ids]

        # Decide an explicit target subset (or None for "all"). Kept as a
        # deterministic function of ``target_choice`` so it composes with builds.
        if target_choice < 0.5:
            targets: List[str] | None = None
        else:
            # A non-empty prefix subset of the seeded SKUs.
            k = 1 + int(target_choice * n) % n if n > 0 else 0
            targets = sku_ids[:k] if k > 0 else None

        return st.builds(
            lambda rows: {
                "rows": rows,
                "scenario": scenario,
                "intensity": intensity,
                "targets": targets,
                "all_ids": sku_ids,
            },
            rows=st.tuples(*row_strats).map(list) if row_strats else st.just([]),
        )

    return st.integers(min_value=1, max_value=5).flatmap(
        lambda n: st.builds(
            build,
            n=st.just(n),
            scenario=st.sampled_from(["demand_spike", "demand_drop", "supply_delay"]),
            intensity=st.floats(
                min_value=1.0, max_value=10.0, allow_nan=False, allow_infinity=False
            ),
            target_choice=st.floats(min_value=0.0, max_value=0.999),
        ).flatmap(lambda s: s)
    )


# --- Property 15 ------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(case=_chaos_case())
def test_chaos_affects_only_the_working_copy(case):
    """Chaos reclassifies a working copy and never touches the source data.

    For each generated dataset + scenario + intensity (+ optional explicit
    target subset):

    - seed the SKUs (fixed base date) and snapshot both inventory tables;
    - run ``apply_chaos``;
    - assert the tables are byte-for-byte unchanged and ``source_unchanged`` is
      True (Req 18.3);
    - assert ``affected_count`` equals the number of targeted SKUs and there is
      one result per target (Req 18.1);
    - assert each result carries valid before/after label lists and moves the
      working-copy input in the documented direction (Req 18.2).

    **Validates: Requirements 18.1, 18.2, 18.3**
    """
    with _temp_session() as session:
        _seed(session, case["rows"])

        expected_targets = (
            case["targets"] if case["targets"] is not None else case["all_ids"]
        )

        before = _snapshot_tables(session)

        request = ChaosRequest(
            scenario=case["scenario"],
            intensity=case["intensity"],
            skus=case["targets"],
        )
        response = apply_chaos(session, request, today=BASE_DATE)

        # Working-copy isolation (Req 18.3): the persisted source is unchanged.
        after = _snapshot_tables(session)
        assert after == before
        assert response.source_unchanged is True

        # Correct targeting (Req 18.1): one result per targeted SKU.
        assert response.affected_count == len(expected_targets)
        assert len(response.results) == len(expected_targets)
        assert [r.sku for r in response.results] == list(expected_targets)

        for result in response.results:
            # Reclassification produced valid label lists on both sides (Req 18.2).
            assert set(result.classifications_before) <= _ALL_LABELS
            assert set(result.classifications_after) <= _ALL_LABELS

            # The working-copy perturbation moved the input in the documented
            # direction (intensity >= 1 so scaling+rounding is monotonic).
            if case["scenario"] == "demand_spike":
                # Sales scaled up -> velocity after >= before; history unchanged
                # length; lead time / current stock untouched by this scenario.
                assert result.sales_velocity_after >= result.sales_velocity_before
                assert result.lead_time_days_after == result.lead_time_days_before
                assert len(result.sales_history_after) == len(
                    result.sales_history_before
                )
                assert all(
                    a >= b
                    for a, b in zip(
                        result.sales_history_after, result.sales_history_before
                    )
                )
            elif case["scenario"] == "demand_drop":
                # Sales scaled down -> velocity after <= before.
                assert result.sales_velocity_after <= result.sales_velocity_before
                assert result.lead_time_days_after == result.lead_time_days_before
            elif case["scenario"] == "supply_delay":
                # Lead time stretched -> after >= before; sales untouched.
                assert result.lead_time_days_after >= result.lead_time_days_before
                assert result.sales_history_after == result.sales_history_before
                assert result.sales_velocity_after == result.sales_velocity_before

            # current_stock is never altered by any chaos scenario.
            assert result.current_stock_after == result.current_stock_before


# --- Focused examples using the shared conftest `session` fixture -----------


def test_unknown_explicit_sku_raises_and_leaves_source_unchanged(session):
    """An explicitly requested unknown SKU raises ``KeyError`` and writes nothing.

    **Validates: Requirements 18.3**
    """
    _seed(
        session,
        [
            SkuRow(
                sku="SKU-A",
                name="Widget A",
                category="general",
                current_stock=100,
                reorder_point=20,
                lead_time_days=7,
                unit_cost=2.5,
                selling_price=4.0,
                supplier_name="Supplier A",
                avg_daily_sales=5.0,
                sales_history=[2, 4, 6, 8],
                last_sold_date="2024-01-01",
            )
        ],
    )
    before = _snapshot_tables(session)

    try:
        apply_chaos(
            session,
            ChaosRequest(scenario="demand_spike", intensity=2.0, skus=["NOPE"]),
            today=BASE_DATE,
        )
        raised = False
    except KeyError:
        raised = True

    assert raised is True
    # Source data untouched despite the failed request (Req 18.3).
    assert _snapshot_tables(session) == before


def test_demand_spike_reclassifies_working_copy(session):
    """A demand spike raises working-copy velocity and can add fast_moving, source intact.

    Seeds a slow steady seller, injects a large demand spike, and asserts the
    after-velocity rose on the working copy and the labels were recomputed, while
    the persisted source rows are unchanged (Req 18.1, 18.2, 18.3).

    **Validates: Requirements 18.1, 18.2, 18.3**
    """
    _seed(
        session,
        [
            SkuRow(
                sku="SKU-A",
                name="Widget A",
                category="general",
                current_stock=1000,
                reorder_point=20,
                lead_time_days=7,
                unit_cost=2.5,
                selling_price=4.0,
                supplier_name="Supplier A",
                avg_daily_sales=5.0,
                sales_history=[5, 5, 5, 5],
                last_sold_date="2024-01-01",
            )
        ],
    )
    before = _snapshot_tables(session)

    response = apply_chaos(
        session,
        ChaosRequest(scenario="demand_spike", intensity=10.0, skus=["SKU-A"]),
        today=BASE_DATE,
    )

    assert response.affected_count == 1
    result = response.results[0]
    # Velocity rose on the working copy: 5/day -> 50/day.
    assert result.sales_velocity_before == 5.0
    assert result.sales_velocity_after == 50.0
    # 50/day > FAST_MOVING_THRESHOLD (20) so fast_moving is now attached.
    assert FAST_MOVING not in result.classifications_before
    assert FAST_MOVING in result.classifications_after
    # Source untouched (Req 18.3).
    assert _snapshot_tables(session) == before
    assert response.source_unchanged is True


def test_supply_delay_stretches_lead_time_working_copy(session):
    """A supply delay stretches working-copy lead time and leaves sales + source intact.

    **Validates: Requirements 18.1, 18.2, 18.3**
    """
    _seed(
        session,
        [
            SkuRow(
                sku="SKU-A",
                name="Widget A",
                category="general",
                current_stock=100,
                reorder_point=20,
                lead_time_days=5,
                unit_cost=2.5,
                selling_price=4.0,
                supplier_name="Supplier A",
                avg_daily_sales=3.0,
                sales_history=[3, 3, 3, 3],
                last_sold_date="2024-01-01",
            )
        ],
    )
    before = _snapshot_tables(session)

    response = apply_chaos(
        session,
        ChaosRequest(scenario="supply_delay", intensity=3.0, skus=["SKU-A"]),
        today=BASE_DATE,
    )

    result = response.results[0]
    assert result.lead_time_days_before == 5
    assert result.lead_time_days_after == 15  # round(5 * 3.0)
    # Sales untouched by a supply delay.
    assert result.sales_history_after == result.sales_history_before
    assert result.sales_velocity_after == result.sales_velocity_before
    # Source untouched (Req 18.3).
    assert _snapshot_tables(session) == before


def test_default_targets_all_skus(session):
    """Omitting ``skus`` affects every SKU in the dataset (Req 18.1).

    **Validates: Requirements 18.1**
    """
    _seed(
        session,
        [
            SkuRow(
                sku=f"SKU-{i}",
                name=f"Widget {i}",
                category="general",
                current_stock=100,
                reorder_point=20,
                lead_time_days=5,
                unit_cost=1.0,
                selling_price=2.0,
                supplier_name="Supplier A",
                avg_daily_sales=2.0,
                sales_history=[2, 2, 2, 2],
                last_sold_date="2024-01-01",
            )
            for i in range(3)
        ],
    )

    response = apply_chaos(
        session,
        ChaosRequest(scenario="demand_drop", intensity=2.0),
        today=BASE_DATE,
    )

    assert response.affected_count == 3
    assert sorted(r.sku for r in response.results) == ["SKU-0", "SKU-1", "SKU-2"]


# --- Subprocess runner (self-check) -----------------------------------------


def _run_pytest_via_subprocess() -> int:
    """Run this test module in a child pytest process and return its exit code.

    Provided per the task's requested self-verification harness: it captures the
    child run's output to a temp file, reads it back, prints it, and cleans up.
    Guarded behind ``__main__`` so it does not recurse when pytest collects this
    module normally.
    """
    fd, out_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        with open(out_path, "w", encoding="utf-8") as out:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", __file__, "-p", "no:cacheprovider", "-q"],
                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                stdout=out,
                stderr=subprocess.STDOUT,
                check=False,
            )
        with open(out_path, "r", encoding="utf-8") as f:
            print(f.read())
        return proc.returncode
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(_run_pytest_via_subprocess())
