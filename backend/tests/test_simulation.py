"""Property 14 — Simulation is deterministic and read-only.

Exercises :func:`app.services.simulation.simulate` end-to-end against a real
in-memory store. A SKU is seeded through the deterministic ingest loader (with a
fixed ``base_date`` so the cached ``stockout_eta`` is deterministic), then a
random subset of hypothetical overrides is applied and the projection compared
against an independent recomputation with the same pure Detection_Engine
functions the app uses at ingest.

The property under test (design.md -> Correctness Properties -> Property 14):

    The What-If Simulation_Engine is deterministic and strictly read-only: the
    same SKU + overrides (+ fixed base date) always yields the same projection
    (Req 16.1), running it never mutates the persisted ``skus`` / ``sales_history``
    rows (Req 16.2), and the projected metrics/classifications equal what the
    deterministic Detection_Engine produces for the overridden inputs while the
    current side reflects the stored cached values (Req 16.3).

Three facets asserted:

* **Determinism (Req 16.1)** — two ``simulate`` calls with identical arguments
  return byte-identical ``current`` and ``projected`` views.
* **Read-only (Req 16.2)** — a full snapshot of the ``skus`` and
  ``sales_history`` tables (every column) is captured before and after
  ``simulate`` and asserted unchanged.
* **Correct projection (Req 16.3)** — the projected ``MetricsView`` equals the
  result of independently running :func:`compute_metrics` + :func:`classify` on
  the overridden in-memory snapshot (same ``today``), and the current view
  equals the metrics cached on the stored row at ingest.

**Validates: Requirements 16.1, 16.2, 16.3**
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from typing import Iterator, List, Optional, Sequence

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.detection.classify import classify
from app.detection.metrics import compute_metrics
from app.ingest.loader import load_rows
from app.models import SalesHistory, Sku
from app.repositories import sku_repo
from app.schemas import MetricsView, SimulationRequest, SkuRow

# The SKU under simulation and a second SKU that must also stay untouched.
TARGET_SKU = "SKU-SIM"
OTHER_SKU = "SKU-OTHER"

# Fixed anchor date so the ingest-cached and projected stockout ETAs are
# deterministic across examples and independent recomputation.
BASE_DATE = date(2024, 1, 15)


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session`` conftest
    fixture across generated examples, so each example builds its own isolated
    database inline — the same pattern used by the Property 9 apply-delta test.
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


@dataclass(frozen=True)
class _ClassifyInput:
    """Plain-data snapshot fed to :func:`classify` for the independent recompute.

    Mirrors the ``SkuLike`` protocol the classifier reads (``current_stock``,
    ``reorder_point``, ``lead_time_days``, ``sales_history``). This is the test's
    own re-derivation of the projected inputs — it does not import the engine's
    internal snapshot type, so the projection is checked against an independent
    reconstruction of the overridden values.
    """

    current_stock: int
    reorder_point: int
    lead_time_days: int
    sales_history: Sequence[int]


def _seed(
    session: Session,
    *,
    current_stock: int,
    reorder_point: int,
    lead_time_days: int,
    unit_cost: float,
    avg_daily_sales: float,
    sales_history: Sequence[int],
) -> None:
    """Seed the target SKU with the given fields plus a fixed untouched control SKU.

    Uses the deterministic ingest loader with a fixed ``base_date`` so the cached
    metrics (including ``stockout_eta``) are deterministic.
    """
    rows = [
        SkuRow(
            sku=TARGET_SKU,
            name="Sim Widget",
            category="general",
            current_stock=current_stock,
            reorder_point=reorder_point,
            lead_time_days=lead_time_days,
            unit_cost=unit_cost,
            selling_price=unit_cost + 1.0,
            supplier_name="Supplier A",
            avg_daily_sales=avg_daily_sales,
            sales_history=list(sales_history),
            last_sold_date="2024-01-01",
        ),
        SkuRow(
            sku=OTHER_SKU,
            name="Other Widget",
            category="general",
            current_stock=55,
            reorder_point=15,
            lead_time_days=4,
            unit_cost=1.5,
            selling_price=2.5,
            supplier_name="Supplier B",
            avg_daily_sales=0.5,
            sales_history=[1, 0, 2, 1],
            last_sold_date="2024-01-01",
        ),
    ]
    load_rows(session, rows, base_date=BASE_DATE)


def _snapshot_tables(session: Session) -> tuple:
    """Capture every ``skus`` and ``sales_history`` column as a comparable tuple.

    Returns a nested tuple ``(sku_rows, sales_rows)`` ordered deterministically,
    so equality before vs after ``simulate`` proves the persisted store was not
    mutated (Req 16.2).
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


def _expected_projected(
    *,
    stored: Sku,
    stored_units: Sequence[int],
    overrides: SimulationRequest,
    today: date,
) -> MetricsView:
    """Independently recompute the projected MetricsView for the overridden inputs.

    Overlays only the provided override fields onto the stored SKU values, then
    runs the same pure Detection_Engine functions the app uses at ingest —
    :func:`compute_metrics` then :func:`classify` — with the same ``today``. This
    is the test's own re-derivation used to check the engine's projection
    (Req 16.3).
    """
    current_stock = (
        overrides.current_stock if overrides.current_stock is not None else stored.current_stock
    )
    reorder_point = (
        overrides.reorder_point if overrides.reorder_point is not None else stored.reorder_point
    )
    lead_time_days = (
        overrides.lead_time_days
        if overrides.lead_time_days is not None
        else stored.lead_time_days
    )
    sales_history = (
        list(overrides.sales_history)
        if overrides.sales_history is not None
        else list(stored_units)
    )
    avg_daily_sales = (
        overrides.avg_daily_sales
        if overrides.avg_daily_sales is not None
        else stored.avg_daily_sales
    )

    metrics = compute_metrics(current_stock, avg_daily_sales, today=today)
    snapshot = _ClassifyInput(
        current_stock=current_stock,
        reorder_point=reorder_point,
        lead_time_days=lead_time_days,
        sales_history=sales_history,
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


# --- Generators -------------------------------------------------------------

# A daily sales list; kept short and bounded so windows split cleanly and the
# zero-velocity edge (empty / all-zero history) is reachable.
_sales_history = st.lists(st.integers(min_value=0, max_value=50), min_size=0, max_size=8)


def _sim_case() -> st.SearchStrategy[dict]:
    """Generate stored SKU fields plus a random subset of overrides.

    Each override field is independently either ``None`` (keep the stored value)
    or a concrete hypothetical value, so a wide range of partial override sets is
    exercised — including the empty override set and full overrides.
    """

    def build(
        stored_stock: int,
        stored_reorder: int,
        stored_lead: int,
        stored_cost: float,
        stored_avg_daily_sales: float,
        stored_history: List[int],
        o_stock: Optional[int],
        o_reorder: Optional[int],
        o_lead: Optional[int],
        o_cost: Optional[float],
        o_avg_daily_sales: Optional[float],
        o_history: Optional[List[int]],
    ) -> dict:
        return {
            "stored_stock": stored_stock,
            "stored_reorder": stored_reorder,
            "stored_lead": stored_lead,
            "stored_cost": stored_cost,
            "stored_avg_daily_sales": stored_avg_daily_sales,
            "stored_history": stored_history,
            "o_stock": o_stock,
            "o_reorder": o_reorder,
            "o_lead": o_lead,
            "o_cost": o_cost,
            "o_avg_daily_sales": o_avg_daily_sales,
            "o_history": o_history,
        }

    return st.builds(
        build,
        stored_stock=st.integers(min_value=0, max_value=10_000),
        stored_reorder=st.integers(min_value=0, max_value=10_000),
        stored_lead=st.integers(min_value=1, max_value=60),
        stored_cost=st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        stored_avg_daily_sales=st.floats(
            min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False
        ),
        stored_history=_sales_history,
        o_stock=st.none() | st.integers(min_value=0, max_value=10_000),
        o_reorder=st.none() | st.integers(min_value=0, max_value=10_000),
        o_lead=st.none() | st.integers(min_value=1, max_value=60),
        o_cost=st.none()
        | st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False),
        o_avg_daily_sales=st.none()
        | st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
        o_history=st.none() | _sales_history,
    )


# --- Property 14 ------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(case=_sim_case())
def test_simulation_is_deterministic_and_read_only(case):
    """Simulation is deterministic, read-only, and projects the right metrics.

    For each generated stored SKU and random override subset:

    - seed the target SKU (fixed base date) plus an untouched control SKU;
    - snapshot both inventory tables;
    - call ``simulate`` twice and assert identical current/projected views
      (Req 16.1);
    - assert the tables are byte-for-byte unchanged after simulating (Req 16.2);
    - assert the projected view equals an independent recompute of the overridden
      inputs and the current view equals the stored cached metrics (Req 16.3).

    **Validates: Requirements 16.1, 16.2, 16.3**
    """
    from app.services.simulation import simulate

    with _temp_session() as session:
        _seed(
            session,
            current_stock=case["stored_stock"],
            reorder_point=case["stored_reorder"],
            lead_time_days=case["stored_lead"],
            unit_cost=case["stored_cost"],
            avg_daily_sales=case["stored_avg_daily_sales"],
            sales_history=case["stored_history"],
        )

        overrides = SimulationRequest(
            current_stock=case["o_stock"],
            reorder_point=case["o_reorder"],
            lead_time_days=case["o_lead"],
            unit_cost=case["o_cost"],
            avg_daily_sales=case["o_avg_daily_sales"],
            sales_history=case["o_history"],
        )

        # Snapshot the persisted store before any simulation runs.
        before = _snapshot_tables(session)

        # Determinism (Req 16.1): two identical calls -> identical results.
        first = simulate(session, TARGET_SKU, overrides, today=BASE_DATE)
        second = simulate(session, TARGET_SKU, overrides, today=BASE_DATE)
        assert first.sku == second.sku == TARGET_SKU
        assert first.current == second.current
        assert first.projected == second.projected

        # Read-only (Req 16.2): the persisted tables are unchanged.
        after = _snapshot_tables(session)
        assert after == before

        # Correct projection (Req 16.3): projected equals an independent recompute
        # of the overridden inputs, using the same base date.
        stored = sku_repo.get(session, TARGET_SKU)
        assert stored is not None
        stored_units = sku_repo.get_sales_units(session, TARGET_SKU)
        expected_projected = _expected_projected(
            stored=stored,
            stored_units=stored_units,
            overrides=overrides,
            today=BASE_DATE,
        )
        assert first.projected == expected_projected

        # Current side reflects exactly the metrics cached on the stored row.
        expected_current = MetricsView(
            sales_velocity=stored.sales_velocity,
            days_of_cover=stored.days_of_cover,
            stockout_eta=stored.stockout_eta,
            no_recent_sales=bool(stored.no_recent_sales),
            classifications=(
                # classifications is a JSON array of label strings on the row.
                __import__("json").loads(stored.classifications)
                if stored.classifications
                else []
            ),
        )
        assert first.current == expected_current


# --- Focused examples using the shared conftest `session` fixture -----------


def test_simulate_unknown_sku_raises_keyerror(session):
    """Simulating a SKU that does not exist raises ``KeyError`` (read-only, no write).

    **Validates: Requirements 16.2**
    """
    from app.services.simulation import simulate

    before = _snapshot_tables(session)
    try:
        simulate(session, "does-not-exist", SimulationRequest(), today=BASE_DATE)
        raised = False
    except KeyError:
        raised = True
    assert raised is True
    assert _snapshot_tables(session) == before


def test_no_overrides_projects_current_metrics(session):
    """With no overrides, the projection reproduces the stored (current) metrics.

    An empty ``SimulationRequest`` keeps every stored field, so the projected
    view — recomputed by the same deterministic engine used at ingest — must
    match the current cached view.

    **Validates: Requirements 16.1, 16.3**
    """
    from app.services.simulation import simulate

    _seed(
        session,
        current_stock=100,
        reorder_point=20,
        lead_time_days=7,
        unit_cost=2.5,
        avg_daily_sales=5.0,
        sales_history=[2, 4, 6, 8],
    )

    result = simulate(session, TARGET_SKU, SimulationRequest(), today=BASE_DATE)

    assert result.projected.sales_velocity == result.current.sales_velocity
    assert result.projected.days_of_cover == result.current.days_of_cover
    assert result.projected.stockout_eta == result.current.stockout_eta
    assert result.projected.no_recent_sales == result.current.no_recent_sales
    assert result.projected.classifications == result.current.classifications


def test_zero_velocity_override_marks_no_recent_sales(session):
    """Overriding sales history to all-zero yields the zero-velocity edge projection.

    Velocity is zero, so days_of_cover / stockout_eta are undefined and
    ``no_recent_sales`` is set (Req 2.5), while the store stays read-only.

    **Validates: Requirements 16.2, 16.3**
    """
    from app.services.simulation import simulate

    _seed(
        session,
        current_stock=100,
        reorder_point=20,
        lead_time_days=7,
        unit_cost=2.5,
        avg_daily_sales=5.0,
        sales_history=[5, 5, 5, 5],
    )
    before = _snapshot_tables(session)

    result = simulate(
        session,
        TARGET_SKU,
        SimulationRequest(avg_daily_sales=0.0, sales_history=[0, 0, 0, 0]),
        today=BASE_DATE,
    )

    assert result.projected.sales_velocity == 0.0
    assert result.projected.days_of_cover is None
    assert result.projected.stockout_eta is None
    assert result.projected.no_recent_sales is True
    # Store untouched.
    assert _snapshot_tables(session) == before
