"""Property 10 — Apply is idempotent (applied at most once).

Exercises :func:`app.services.apply_action.apply` end-to-end against a real
in-memory store. A SKU is seeded through the deterministic ingest loader, a
pending ``reorder`` recommendation is created, approved via
:mod:`app.services.review`, then applied. The *first* apply performs the exact
delta (stock increases by the reorder quantity, status becomes ``applied``).
Every *subsequent* apply must be a no-op success: the inventory is not mutated a
second time, the status stays ``applied``, and each call returns ``applied=True``.

The property under test (design.md → Correctness Properties → Property 10):

    Applying an already-applied recommendation is a no-op success. The action
    runs at most once per recommendation — inventory is mutated exactly once,
    when the recommendation first transitions to ``applied``; any number of
    later apply calls leave stock and status unchanged.

**Validates: Requirements 11.4**
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


def _create_pending_reorder(
    session: Session,
    *,
    quantity: int,
) -> int:
    """Create a case + a pending ``reorder`` recommendation for the target SKU; return its id."""
    case = case_repo.create(session, sku=TARGET_SKU, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=TARGET_SKU,
        action_kind=ActionKind.REORDER.value,
        rationale="test reorder recommendation",
        quantity=quantity,
        new_reorder_point=None,
    )
    return rec.id


# --- Property 10 ------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(
    quantity=st.integers(min_value=1, max_value=10_000),
    target_stock=st.integers(min_value=0, max_value=10_000),
    target_reorder=st.integers(min_value=0, max_value=10_000),
    reapply_n=st.integers(min_value=1, max_value=5),
)
def test_apply_is_idempotent(quantity, target_stock, target_reorder, reapply_n):
    """Applying an approved reorder mutates once; re-applying is a no-op success.

    For each generated (quantity, stock, reorder-point, N):

    - seed the target SKU (known stock/reorder) plus an untouched control SKU;
    - create a pending ``reorder`` recommendation, approve it;
    - apply ONCE: assert stock == old_stock + quantity and status ``applied``;
    - apply AGAIN N times (N in 1..5): assert stock is UNCHANGED after the first
      apply (no repeated delta), status stays ``applied``, and every call returns
      ``applied=True`` with status ``applied`` (no-op success);
    - assert the control SKU is byte-for-byte unchanged throughout.

    **Validates: Requirements 11.4**
    """
    with _temp_session() as session:
        _seed(session, target_stock=target_stock, target_reorder=target_reorder)

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

        rec_id = _create_pending_reorder(session, quantity=quantity)

        # pending -> approved -> applied (first apply performs the delta).
        review.approve(session, rec_id)
        first = apply_action.apply(session, rec_id)

        assert first.applied is True
        assert first.status == RecommendationStatus.APPLIED

        # The single mutation: stock increased by exactly the reorder quantity.
        applied_stock = old_stock + quantity
        target_after_first = sku_repo.get(session, TARGET_SKU)
        assert target_after_first is not None
        assert target_after_first.current_stock == applied_stock
        assert target_after_first.reorder_point == old_reorder

        rec = recommendation_repo.get(session, rec_id)
        assert rec is not None
        assert rec.status == RecommendationStatus.APPLIED.value

        # Apply AGAIN N times: each is a no-op success (applied at most once).
        for _ in range(reapply_n):
            again = apply_action.apply(session, rec_id)

            # No-op success: returns applied=True, status stays applied.
            assert again.applied is True
            assert again.status == RecommendationStatus.APPLIED
            assert again.error is None

            # Inventory is UNCHANGED — no repeated delta on the target SKU.
            target_now = sku_repo.get(session, TARGET_SKU)
            assert target_now is not None
            assert target_now.current_stock == applied_stock
            assert target_now.reorder_point == old_reorder

            # Status remains applied.
            rec_now = recommendation_repo.get(session, rec_id)
            assert rec_now is not None
            assert rec_now.status == RecommendationStatus.APPLIED.value

        # The control SKU is completely untouched throughout.
        other_after = sku_repo.get(session, OTHER_SKU)
        assert other_after is not None
        assert other_after.current_stock == other_stock_before
        assert other_after.reorder_point == other_reorder_before


# --- Focused example using the shared conftest `session` fixture ------------


def test_double_apply_does_not_double_the_stock(session):
    """A concrete reorder applied twice increases stock only once.

    Seeds stock 100, a pending reorder of 50, approves and applies: stock -> 150.
    A second apply returns a no-op success and leaves stock at 150 (not 200), the
    status ``applied``, and the control SKU untouched.

    **Validates: Requirements 11.4**
    """
    _seed(session, target_stock=100, target_reorder=20)
    rec_id = _create_pending_reorder(session, quantity=50)

    review.approve(session, rec_id)

    first = apply_action.apply(session, rec_id)
    assert first.applied is True
    assert first.status == RecommendationStatus.APPLIED
    assert sku_repo.get(session, TARGET_SKU).current_stock == 150

    # Re-apply: no-op success, stock does NOT become 200.
    second = apply_action.apply(session, rec_id)
    assert second.applied is True
    assert second.status == RecommendationStatus.APPLIED
    assert second.error is None

    target = sku_repo.get(session, TARGET_SKU)
    assert target.current_stock == 150
    assert target.reorder_point == 20

    rec = recommendation_repo.get(session, rec_id)
    assert rec.status == RecommendationStatus.APPLIED.value

    other = sku_repo.get(session, OTHER_SKU)
    assert other.current_stock == OTHER_STOCK
    assert other.reorder_point == OTHER_REORDER
