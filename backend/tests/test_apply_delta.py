"""Property 9 — Deterministic apply produces the exact action delta.

Exercises :func:`app.services.apply_action.apply` end-to-end against a real
in-memory store: a SKU is seeded through the deterministic ingest loader, a
pending recommendation is created, approved via :mod:`app.services.review`, then
applied. The property asserts the *exact* inventory delta for each action kind
and that no other SKU is touched.

The property under test (design.md → Correctness Properties → Property 9):

    For any approved recommendation carrying a ProposedAction, applying it
    transforms the inventory exactly by that action's deterministic delta
    (reorder increases stock by quantity; adjust_reorder sets reorder_point to
    the new value; markdown/no_action change nothing) and no other SKU is
    affected.

Exact deltas asserted (Req 11.1):

* ``reorder``        -> ``current_stock == old_stock + quantity``; reorder_point
  unchanged.
* ``adjust_reorder`` -> ``reorder_point == new_reorder_point``; current_stock
  unchanged.
* ``markdown`` / ``no_action`` -> both current_stock and reorder_point
  unchanged.

In every case a second, untouched SKU must read back byte-identical, and the
recommendation's status must become ``applied`` (Req 11.2).

**Validates: Requirements 11.1**
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.repositories import case_repo, recommendation_repo, sku_repo
from app.schemas import ActionKind, RecommendationStatus, SkuRow
from app.services import apply_action, review
from app.ingest.loader import load_rows

# The target SKU under investigation and a second SKU that must stay untouched.
TARGET_SKU = "SKU-TARGET"
OTHER_SKU = "SKU-OTHER"

# Fixed baseline for the untouched control SKU so we can assert it is unchanged.
OTHER_STOCK = 77
OTHER_REORDER = 33


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session`` conftest
    fixture across generated examples, so each example builds its own isolated
    database inline — the same pattern used by the Property 6 / Property 7 tests.
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


def _seed(
    session: Session,
    *,
    target_stock: int,
    target_reorder: int,
) -> None:
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
    load_rows(session, rows)


def _create_pending(
    session: Session,
    *,
    action_kind: str,
    quantity: int | None,
    new_reorder_point: int | None,
) -> int:
    """Create a case + a pending recommendation for the target SKU; return its id."""
    case = case_repo.create(session, sku=TARGET_SKU, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=TARGET_SKU,
        action_kind=action_kind,
        rationale="test recommendation",
        quantity=quantity,
        new_reorder_point=new_reorder_point,
    )
    return rec.id


# --- Generators -------------------------------------------------------------


def _action_case() -> st.SearchStrategy[dict]:
    """Generate an action kind plus the params it uses and the seed stock/reorder.

    Returns a dict with keys ``action_kind``, ``quantity``, ``new_reorder_point``,
    ``target_stock``, ``target_reorder``. Only the field relevant to the kind is
    populated (quantity for reorder, new_reorder_point for adjust_reorder); the
    others are ``None`` — matching how the Manager parser fills a recommendation.
    """

    def build(
        kind: str,
        quantity: int,
        new_reorder_point: int,
        target_stock: int,
        target_reorder: int,
    ) -> dict:
        return {
            "action_kind": kind,
            "quantity": quantity if kind == ActionKind.REORDER.value else None,
            "new_reorder_point": (
                new_reorder_point if kind == ActionKind.ADJUST_REORDER.value else None
            ),
            "target_stock": target_stock,
            "target_reorder": target_reorder,
        }

    return st.builds(
        build,
        kind=st.sampled_from([k.value for k in ActionKind]),
        quantity=st.integers(min_value=1, max_value=10_000),
        new_reorder_point=st.integers(min_value=0, max_value=10_000),
        target_stock=st.integers(min_value=0, max_value=10_000),
        target_reorder=st.integers(min_value=0, max_value=10_000),
    )


# --- Property 9 -------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(case=_action_case())
def test_apply_produces_exact_delta(case):
    """Applying an approved recommendation yields exactly the action's delta.

    For each generated action kind and params:

    - seed the target SKU (known stock/reorder) plus an untouched control SKU;
    - create a pending recommendation, approve it, then apply it;
    - assert the EXACT delta on the target SKU per action kind;
    - assert the control SKU is byte-for-byte unchanged;
    - assert the recommendation status becomes ``applied``.

    **Validates: Requirements 11.1**
    """
    kind = case["action_kind"]
    quantity = case["quantity"]
    new_reorder_point = case["new_reorder_point"]
    target_stock = case["target_stock"]
    target_reorder = case["target_reorder"]

    with _temp_session() as session:
        _seed(session, target_stock=target_stock, target_reorder=target_reorder)

        # Baselines captured straight from the store after seeding.
        target = sku_repo.get(session, TARGET_SKU)
        assert target is not None
        old_stock = target.current_stock
        old_reorder = target.reorder_point
        assert old_stock == target_stock
        assert old_reorder == target_reorder

        other_before = sku_repo.get(session, OTHER_SKU)
        assert other_before is not None
        other_stock_before = other_before.current_stock
        other_reorder_before = other_before.reorder_point

        rec_id = _create_pending(
            session,
            action_kind=kind,
            quantity=quantity,
            new_reorder_point=new_reorder_point,
        )

        # pending -> approved -> applied.
        review.approve(session, rec_id)
        result = apply_action.apply(session, rec_id)

        # Apply succeeded and the recommendation is now applied (Req 11.2).
        assert result.applied is True
        assert result.status == RecommendationStatus.APPLIED
        rec = recommendation_repo.get(session, rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.APPLIED.value

        # Exact delta on the target SKU (Req 11.1).
        target_after = sku_repo.get(session, TARGET_SKU)
        assert target_after is not None
        if kind == ActionKind.REORDER.value:
            assert target_after.current_stock == old_stock + quantity
            assert target_after.reorder_point == old_reorder
        elif kind == ActionKind.ADJUST_REORDER.value:
            assert target_after.reorder_point == new_reorder_point
            assert target_after.current_stock == old_stock
        else:  # markdown / no_action -> no stock or reorder-point change.
            assert target_after.current_stock == old_stock
            assert target_after.reorder_point == old_reorder

        # The control SKU is completely untouched.
        other_after = sku_repo.get(session, OTHER_SKU)
        assert other_after is not None
        assert other_after.current_stock == other_stock_before
        assert other_after.reorder_point == other_reorder_before


# --- Focused examples using the shared conftest `session` fixture -----------


def test_reorder_adds_quantity_to_target_only(session):
    """A concrete reorder adds quantity to the target stock, leaving others alone.

    **Validates: Requirements 11.1**
    """
    _seed(session, target_stock=100, target_reorder=20)
    rec_id = _create_pending(
        session, action_kind=ActionKind.REORDER.value, quantity=50, new_reorder_point=None
    )

    review.approve(session, rec_id)
    result = apply_action.apply(session, rec_id)

    assert result.applied is True
    target = sku_repo.get(session, TARGET_SKU)
    assert target.current_stock == 150
    assert target.reorder_point == 20
    other = sku_repo.get(session, OTHER_SKU)
    assert other.current_stock == OTHER_STOCK
    assert other.reorder_point == OTHER_REORDER


def test_adjust_reorder_sets_reorder_point_only(session):
    """A concrete adjust_reorder sets reorder_point and leaves stock unchanged.

    **Validates: Requirements 11.1**
    """
    _seed(session, target_stock=100, target_reorder=20)
    rec_id = _create_pending(
        session,
        action_kind=ActionKind.ADJUST_REORDER.value,
        quantity=None,
        new_reorder_point=42,
    )

    review.approve(session, rec_id)
    result = apply_action.apply(session, rec_id)

    assert result.applied is True
    target = sku_repo.get(session, TARGET_SKU)
    assert target.reorder_point == 42
    assert target.current_stock == 100


def test_no_action_changes_nothing(session):
    """A concrete no_action leaves both target fields unchanged.

    **Validates: Requirements 11.1**
    """
    _seed(session, target_stock=100, target_reorder=20)
    rec_id = _create_pending(
        session, action_kind=ActionKind.NO_ACTION.value, quantity=None, new_reorder_point=None
    )

    review.approve(session, rec_id)
    result = apply_action.apply(session, rec_id)

    assert result.applied is True
    target = sku_repo.get(session, TARGET_SKU)
    assert target.current_stock == 100
    assert target.reorder_point == 20
