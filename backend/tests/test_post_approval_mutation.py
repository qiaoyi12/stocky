"""Property 11 — Only the deterministic apply path (post-approval) changes inventory.

This is the capstone of STOCKY's safety story. Properties 4 and 9 established the
two halves separately: agents/orchestration never mutate inventory, and the
deterministic apply produces the exact delta. Property 11 ties them together on a
single store snapshot to assert the *ordering* guarantee end to end:

    For any store state and flagged SKU, running an investigation
    (orchestration) and a what-if simulation — WITHOUT the approve+apply step —
    leaves the ``skus`` and ``sales_history`` tables byte-for-byte unchanged.
    Inventory changes only once the approved recommendation is deterministically
    applied, and only then.

Concretely, for each generated example we assert the timeline:

* orchestration alone  -> inventory byte-for-byte unchanged (only a ``pending``
  recommendation row is added);
* simulation           -> inventory byte-for-byte unchanged (read-only);
* approve + apply       -> inventory changes now, and exactly by the reorder delta.

The Manager output is driven by a :class:`FakeLLMClient` seeded with a strict-JSON
``reorder`` recommendation so the eventual apply produces a *visible* stock change
— proving the "changed only now" half of the property is not vacuously true.

**Validates: Requirements 9.3, 16.2**
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from datetime import date
from typing import Iterator, Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.context import SkuSnapshot
from app.agents.orchestrator import run_investigation
from app.ingest.loader import load_rows
from app.repositories import recommendation_repo, sku_repo
from app.schemas import RecommendationStatus, SimulationRequest, SkuRow
from app.services import apply_action, review
from app.services.simulation import simulate

from tests.conftest import FakeLLMClient

# The SKU under investigation and a second SKU that must stay untouched at every
# step (proves the apply delta is scoped to the target only).
TARGET_SKU = "SKU-TARGET"
OTHER_SKU = "SKU-OTHER"

OTHER_STOCK = 77
OTHER_REORDER = 33

# Fixed anchor so the ingest-cached metrics (and stockout_eta) are deterministic
# across examples — the inventory snapshot must be stable to compare byte-for-byte.
BASE_DATE = date(2024, 1, 1)


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session`` conftest
    fixture across generated examples, so each example builds its own isolated
    database inline — the same pattern used by test_apply_delta.py.
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


def _seed(session: Session, *, target_stock: int, target_reorder: int) -> None:
    """Seed the target SKU (given stock/reorder) and a fixed untouched control SKU."""
    rows = [
        SkuRow(
            sku=TARGET_SKU,
            name="Target Widget",
            category="general",
            current_stock=target_stock,
            reorder_point=target_reorder,
            lead_time_days=7,
            unit_cost=2.5,
            selling_price=4.0,
            supplier_name="Supplier A",
            avg_daily_sales=2.5,
            sales_history=[1, 2, 3, 4],
            last_sold_date="2024-01-01",
        ),
        SkuRow(
            sku=OTHER_SKU,
            name="Other Widget",
            category="general",
            current_stock=OTHER_STOCK,
            reorder_point=OTHER_REORDER,
            lead_time_days=5,
            unit_cost=1.0,
            selling_price=1.5,
            supplier_name="Supplier B",
            avg_daily_sales=0.75,
            sales_history=[0, 1, 0, 2],
            last_sold_date="2024-01-01",
        ),
    ]
    load_rows(session, rows, base_date=BASE_DATE)


def _snapshot_inventory(session: Session) -> Tuple[tuple, tuple]:
    """Return a byte-for-byte comparable snapshot of skus + sales_history.

    Each row is captured as a tuple of every column value, ordered
    deterministically, so equality comparison detects any mutation, insert, or
    delete on the inventory tables. Mirrors the snapshot used by the Property 4
    safety-boundary test.
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
        for r in sku_repo.list_all(session)
    )
    history_rows = tuple(
        (h.id, h.sku, h.day, h.units)
        for sku_row in sku_repo.list_all(session)
        for h in sku_repo.get_sales_history(session, sku_row.sku)
    )
    return sku_rows, history_rows


def _snapshot_from_row(session: Session, sku: str) -> SkuSnapshot:
    """Build a plain-data :class:`SkuSnapshot` from a persisted SKU row."""
    import json

    row = sku_repo.get(session, sku)
    assert row is not None
    return SkuSnapshot(
        sku=row.sku,
        name=row.name,
        category=row.category,
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        unit_cost=row.unit_cost,
        sales_history=sku_repo.get_sales_units(session, sku),
        sales_velocity=row.sales_velocity,
        days_of_cover=row.days_of_cover,
        stockout_eta=row.stockout_eta,
        no_recent_sales=bool(row.no_recent_sales),
        classifications=json.loads(row.classifications or "[]"),
    )


def _manager_reorder_json(sku: str, quantity: int) -> str:
    """A strict-JSON Manager response proposing a visible reorder for ``sku``."""
    return (
        '{"action_kind": "reorder", "sku": "%s", "quantity": %d, '
        '"rationale": "Stock is low relative to velocity; reorder to restock."}'
        % (sku, quantity)
    )


# --- Property 11 ------------------------------------------------------------


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(
    target_stock=st.integers(min_value=0, max_value=10_000),
    target_reorder=st.integers(min_value=0, max_value=1_000),
    quantity=st.integers(min_value=1, max_value=10_000),
    sim_stock=st.integers(min_value=0, max_value=10_000),
    sim_reorder=st.integers(min_value=0, max_value=1_000),
)
def test_only_post_approval_apply_changes_inventory(
    target_stock: int,
    target_reorder: int,
    quantity: int,
    sim_stock: int,
    sim_reorder: int,
) -> None:
    """Inventory is unchanged by orchestration + simulation; it changes only on apply.

    Timeline asserted on a single store:

    1. Seed target + control SKUs and snapshot the inventory tables.
    2. Run the full investigation (orchestration) with a reorder-proposing
       Manager -> inventory byte-for-byte unchanged; a single ``pending``
       recommendation is added.
    3. Run a what-if simulation with overrides -> inventory byte-for-byte
       unchanged (read-only, Req 16.2).
    4. Approve + apply the pending recommendation -> inventory changes NOW, and
       exactly by the reorder delta on the target SKU; the control SKU is
       untouched (Req 9.3).

    **Validates: Requirements 9.3, 16.2**
    """
    with _temp_session() as session:
        _seed(session, target_stock=target_stock, target_reorder=target_reorder)
        session.commit()

        baseline = _snapshot_inventory(session)
        old_stock = sku_repo.get(session, TARGET_SKU).current_stock
        old_reorder = sku_repo.get(session, TARGET_SKU).reorder_point

        # --- Step 2: orchestration alone must not mutate inventory ----------
        snapshot = _snapshot_from_row(session, TARGET_SKU)
        client = FakeLLMClient(manager_json=_manager_reorder_json(TARGET_SKU, quantity))
        result = run_investigation(
            session, snapshot, list(snapshot.classifications), client=client
        )
        session.commit()

        assert result.status == "complete"
        assert _snapshot_inventory(session) == baseline, (
            "orchestration must not change inventory before approval/apply"
        )
        # The only new row is a single pending recommendation.
        recs = recommendation_repo.list_all(session)
        assert len(recs) == 1
        assert recs[0].status == RecommendationStatus.PENDING.value
        rec_id = result.recommendation_id
        assert rec_id is not None

        # --- Step 3: simulation is read-only; still no mutation -------------
        sim = simulate(
            session,
            TARGET_SKU,
            SimulationRequest(current_stock=sim_stock, reorder_point=sim_reorder),
            today=BASE_DATE,
        )
        session.commit()
        assert sim.sku == TARGET_SKU  # ran a real projection
        assert _snapshot_inventory(session) == baseline, (
            "simulation must be read-only (Req 16.2)"
        )
        # Recommendation still pending — nothing applied yet.
        assert recommendation_repo.get(session, rec_id).status == (
            RecommendationStatus.PENDING.value
        )

        # --- Step 4: approve + apply is the ONLY thing that changes stock ---
        review.approve(session, rec_id)
        # Approval alone (pre-apply) still hasn't touched inventory.
        assert _snapshot_inventory(session) == baseline, (
            "approval without apply must not change inventory"
        )

        apply_result = apply_action.apply(session, rec_id)
        session.commit()

        assert apply_result.applied is True
        assert apply_result.status == RecommendationStatus.APPLIED

        # Inventory changed NOW, and exactly by the reorder delta on the target.
        after = _snapshot_inventory(session)
        assert after != baseline, "apply must change inventory"

        target_after = sku_repo.get(session, TARGET_SKU)
        assert target_after.current_stock == old_stock + quantity
        assert target_after.reorder_point == old_reorder

        # The control SKU is byte-for-byte untouched throughout.
        other_after = sku_repo.get(session, OTHER_SKU)
        assert other_after.current_stock == OTHER_STOCK
        assert other_after.reorder_point == OTHER_REORDER


# --- Focused example using the shared conftest `session` fixture ------------


def test_reorder_applied_only_after_approval(session) -> None:
    """A concrete walk-through: orchestrate, simulate, then approve+apply.

    Demonstrates the same timeline with fixed numbers for readability:
    orchestration and simulation leave stock at 100; only the post-approval
    apply raises it to 100 + 60.

    **Validates: Requirements 9.3, 16.2**
    """
    _seed(session, target_stock=100, target_reorder=20)
    session.commit()

    baseline = _snapshot_inventory(session)

    snapshot = _snapshot_from_row(session, TARGET_SKU)
    client = FakeLLMClient(manager_json=_manager_reorder_json(TARGET_SKU, 60))
    result = run_investigation(
        session, snapshot, list(snapshot.classifications), client=client
    )
    session.commit()

    assert result.status == "complete"
    # Orchestration changed nothing; stock still 100.
    assert _snapshot_inventory(session) == baseline
    assert sku_repo.get(session, TARGET_SKU).current_stock == 100

    # Simulation is read-only; stock still 100.
    simulate(
        session,
        TARGET_SKU,
        SimulationRequest(current_stock=5, sales_history=[50, 50, 50, 50]),
        today=BASE_DATE,
    )
    session.commit()
    assert _snapshot_inventory(session) == baseline
    assert sku_repo.get(session, TARGET_SKU).current_stock == 100

    # Approve + apply — stock changes only now.
    rec_id = result.recommendation_id
    review.approve(session, rec_id)
    assert sku_repo.get(session, TARGET_SKU).current_stock == 100  # approval alone
    apply_action.apply(session, rec_id)
    session.commit()

    assert sku_repo.get(session, TARGET_SKU).current_stock == 160
    assert sku_repo.get(session, OTHER_SKU).current_stock == OTHER_STOCK
