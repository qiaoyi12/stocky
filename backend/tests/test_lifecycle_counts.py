"""Property 13 — Aggregate lifecycle counts are consistent.

Exercises the two lifecycle-status aggregators — the Dashboard
(:func:`app.services.dashboard.build_dashboard`, field ``lifecycle_counts``) and
the Impact page (:func:`app.services.dashboard.build_impact`, field
``outcome_counts``) — and asserts the invariant the design's Correctness
Properties → Property 13 states:

    For any set of recommendations, the per-status counts reported by the
    Dashboard and Impact page equal the actual number of recommendations in each
    status, and those counts sum to the total number of recommendations.

Approach
--------
A single SKU (+ its case) is seeded through the deterministic loader so every
generated recommendation has a valid ``sku`` / ``case_id`` foreign key. Hypothesis
then draws a list of target final statuses; for each one a fresh ``pending``
recommendation is created and driven to its target through the **service path**:

* ``pending``  — left as created (the seed state);
* ``approved`` — :func:`app.services.review.approve`;
* ``rejected`` — :func:`app.services.review.reject`;
* ``applied``  — :func:`app.services.review.approve` then
  :func:`app.services.apply_action.apply`.

Driving through the real review/apply services (rather than writing statuses
directly) keeps the counted state on the sanctioned lifecycle path. Each
recommendation is a ``reorder`` so the ``approved -> applied`` step performs a
genuine inventory delta on the target SKU, exercising the full success
transition. The expected per-status tally is accumulated as the recommendations
are driven, giving an independent oracle to compare both aggregators against.

Property 13 assertions, for the generated set:

* ``build_dashboard(session).lifecycle_counts`` equals the expected per-status
  tally (Req 13.2);
* ``build_impact(session).outcome_counts`` equals the same tally (Req 17.1), so
  the two pages agree;
* every lifecycle status (``pending``/``approved``/``rejected``/``applied``) is
  present in both aggregators with a count ``>= 0`` (seeded complete picture);
* the counts sum to the total number of recommendations created.

LLM calls are never involved — recommendations are seeded directly through the
repositories and moved through the deterministic review/apply services, so this
property runs fast and offline.

**Validates: Requirements 13.2, 17.1**
"""

from __future__ import annotations

import os
import tempfile
from collections import Counter
from contextlib import contextmanager
from typing import Iterator, List

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine

from app.ingest.loader import load_rows
from app.repositories import case_repo, recommendation_repo
from app.schemas import ActionKind, RecommendationStatus, SkuRow
from app.services import apply_action, review
from app.services.dashboard import build_dashboard, build_impact

# The closed set of lifecycle statuses (Req 12.1); every one must appear in the
# seeded aggregators even at count zero.
ALL_STATUSES = [status.value for status in RecommendationStatus]

PENDING = RecommendationStatus.PENDING.value
APPROVED = RecommendationStatus.APPROVED.value
REJECTED = RecommendationStatus.REJECTED.value
APPLIED = RecommendationStatus.APPLIED.value

SKU_ID = "SKU-1"


# --- Test database + seeding helpers ----------------------------------------


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot reuse the function-scoped ``session`` conftest
    fixture (it would be shared across every generated example), so each example
    builds its own isolated database inline — the same pattern used by
    ``tests/test_lifecycle.py`` and ``test_agent_order.py``.
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


def _seed_sku(session: Session) -> None:
    """Seed a single SKU (+ sales history) via the deterministic loader.

    Every generated recommendation's ``sku`` foreign key points at this row, and
    the ``approved -> applied`` apply path mutates its ``current_stock``.
    """
    load_rows(
        session,
        [
            SkuRow(
                sku=SKU_ID,
                name="Test Widget",
                category="general",
                current_stock=10,
                reorder_point=5,
                lead_time_days=7,
                unit_cost=2.5,
                selling_price=4.0,
                supplier_name="Supplier A",
                avg_daily_sales=2.5,
                sales_history=[1, 2, 3, 4],
                last_sold_date="2024-01-01",
            )
        ],
    )


def _create_pending(session: Session, case_id: int) -> int:
    """Create one fresh ``pending`` ``reorder`` recommendation; return its id.

    ``reorder`` is used so the ``approved -> applied`` step performs a genuine
    inventory delta, exercising the full success transition rather than the
    ``no_action`` no-op branch.
    """
    rec = recommendation_repo.create_pending(
        session,
        case_id=case_id,
        sku=SKU_ID,
        action_kind=ActionKind.REORDER.value,
        rationale="Reorder to cover projected demand.",
        quantity=20,
    )
    return rec.id


def _drive_to(session: Session, recommendation_id: int, target: str) -> None:
    """Drive a freshly-created pending recommendation to ``target`` via services.

    Uses only legal transitions through the real review/apply service path:

    * ``pending``  -> no-op (already there);
    * ``approved`` -> :func:`review.approve`;
    * ``rejected`` -> :func:`review.reject`;
    * ``applied``  -> :func:`review.approve` then :func:`apply_action.apply`.
    """
    if target == PENDING:
        return
    if target == APPROVED:
        review.approve(session, recommendation_id)
    elif target == REJECTED:
        review.reject(session, recommendation_id)
    elif target == APPLIED:
        review.approve(session, recommendation_id)
        apply_action.apply(session, recommendation_id)
    else:  # pragma: no cover - defensive; targets are constrained by generators
        raise AssertionError(f"unsupported target status: {target!r}")


# --- Generators -------------------------------------------------------------

# A generated list of final statuses, one per recommendation to create. Empty
# lists are allowed (zero recommendations) so the "all statuses present at zero"
# edge is covered; the cap keeps each example fast while still driving real
# service transitions per recommendation.
_TARGET_STATUSES = st.lists(
    st.sampled_from([PENDING, APPROVED, REJECTED, APPLIED]),
    min_size=0,
    max_size=25,
)


# --- Property 13 ------------------------------------------------------------


@settings(deadline=None, max_examples=100)
@given(targets=_TARGET_STATUSES)
def test_lifecycle_counts_consistent(targets: List[str]):
    """Dashboard and Impact per-status counts equal the actual tally (Prop 13).

    Seeds one SKU + case, then for each generated target status creates a fresh
    pending recommendation and drives it to that status through the review/apply
    services, accumulating an independent expected tally. Asserts:

    * both aggregators report every lifecycle status, each count ``>= 0``;
    * ``build_dashboard.lifecycle_counts`` equals the expected tally (Req 13.2);
    * ``build_impact.outcome_counts`` equals the same tally (Req 17.1), so the
      Dashboard and Impact page agree;
    * the counts sum to the total number of recommendations created.

    **Validates: Requirements 13.2, 17.1**
    """
    with _temp_session() as session:
        _seed_sku(session)
        case = case_repo.create(session, sku=SKU_ID, status="complete")

        # Independent oracle: tally the intended final status of each rec as we
        # drive it through the real service transitions.
        expected = Counter({status: 0 for status in ALL_STATUSES})
        for target in targets:
            rec_id = _create_pending(session, case.id)
            _drive_to(session, rec_id, target)
            expected[target] += 1

        expected_counts = dict(expected)

        dashboard = build_dashboard(session)
        impact = build_impact(session)

        # Every lifecycle status is present in both aggregators, count >= 0
        # (the seeded complete-picture guarantee).
        for status in ALL_STATUSES:
            assert status in dashboard.lifecycle_counts
            assert status in impact.outcome_counts
            assert dashboard.lifecycle_counts[status] >= 0
            assert impact.outcome_counts[status] >= 0

        # Dashboard lifecycle_counts equal the actual per-status tally (Req 13.2).
        assert dashboard.lifecycle_counts == expected_counts

        # Impact outcome_counts equal the same tally, so the pages agree (Req 17.1).
        assert impact.outcome_counts == expected_counts

        # Counts sum to the total number of recommendations created.
        assert sum(dashboard.lifecycle_counts.values()) == len(targets)
        assert sum(impact.outcome_counts.values()) == len(targets)


# --- Focused example using the shared conftest `session` fixture ------------


def test_lifecycle_counts_all_statuses_present_when_empty(session):
    """With no recommendations, both aggregators seed every status at zero.

    **Validates: Requirements 13.2, 17.1**
    """
    _seed_sku(session)

    dashboard = build_dashboard(session)
    impact = build_impact(session)

    for status in ALL_STATUSES:
        assert dashboard.lifecycle_counts.get(status) == 0
        assert impact.outcome_counts.get(status) == 0

    assert sum(dashboard.lifecycle_counts.values()) == 0
    assert sum(impact.outcome_counts.values()) == 0


def test_lifecycle_counts_mixed_statuses(session):
    """Concrete mixed tally: one of each status is counted correctly.

    Drives four recommendations to pending / approved / rejected / applied and
    asserts both aggregators report exactly one in each status.

    **Validates: Requirements 13.2, 17.1**
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")

    for target in (PENDING, APPROVED, REJECTED, APPLIED):
        rec_id = _create_pending(session, case.id)
        _drive_to(session, rec_id, target)

    expected = {PENDING: 1, APPROVED: 1, REJECTED: 1, APPLIED: 1}

    dashboard = build_dashboard(session)
    impact = build_impact(session)

    assert dashboard.lifecycle_counts == expected
    assert impact.outcome_counts == expected
    assert sum(dashboard.lifecycle_counts.values()) == 4
