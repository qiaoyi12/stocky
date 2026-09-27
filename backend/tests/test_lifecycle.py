"""Property 8 — Lifecycle validity.

Exercises the recommendation lifecycle through the *service layer* — the review
service (:func:`app.services.review.approve` / ``reject`` / ``transition``) and
the deterministic apply service (:func:`app.services.apply_action.apply`) — and
asserts the invariants the design's Correctness Properties → Property 8 states:

    For any recommendation, its status is always one of
    {pending, approved, rejected, applied}; a transition takes effect only if it
    is one of pending→approved, pending→rejected, or approved→applied; and any
    other requested transition (including approve/reject on a non-pending
    recommendation) is refused with the current status retained.

The single source of legality is ``services.review.LEGAL`` (the closed set of
allowed ``(current, target)`` pairs). The properties below drive real
transitions through the service functions and assert that:

* a transition is accepted **iff** ``(start, target)`` is in ``LEGAL``;
* an illegal transition raises :class:`IllegalTransition` and leaves the stored
  status unchanged (Req 10.4, 12.3);
* driving a full legal sequence persists each status correctly, and the status
  is always drawn from the closed set (Req 10.2, 10.3, 11.2, 12.1);
* once a recommendation reaches a terminal status (``rejected``/``applied``) any
  further transition attempt is refused and the status is unchanged
  (Req 12.2, 12.3).

LLM calls are never involved here — recommendations are seeded directly through
the repositories, so these deterministic lifecycle properties run fast and
offline.

**Validates: Requirements 10.2, 10.3, 10.4, 12.1, 12.2, 12.3, 11.2**
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.ingest.loader import load_rows
from app.repositories import case_repo, recommendation_repo
from app.schemas import ActionKind, RecommendationStatus, SkuRow
from app.services import apply_action, review
from app.services.review import LEGAL, IllegalTransition

# The closed set of lifecycle statuses (Req 12.1). A recommendation's status
# must always be a member of this set.
CLOSED_STATUSES = {status.value for status in RecommendationStatus}

PENDING = RecommendationStatus.PENDING.value
APPROVED = RecommendationStatus.APPROVED.value
REJECTED = RecommendationStatus.REJECTED.value
APPLIED = RecommendationStatus.APPLIED.value

# A fixed SKU id used for the seeded inventory row.
SKU_ID = "SKU-1"


# --- Test database + seeding helpers ----------------------------------------


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot be combined with the function-scoped
    ``session`` conftest fixture (it would be reused across every generated
    example), so each example builds its own isolated database inline — the same
    pattern used by ``tests/test_agent_order.py`` and ``test_manager_pending.py``.
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
    """Seed a single SKU with a sales history via the deterministic loader.

    The recommendation's ``sku`` foreign key points at this row, and the apply
    path (approved -> applied for a ``reorder``) mutates its ``current_stock``.
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


def _seed_pending(session: Session) -> int:
    """Seed a case + a pending ``reorder`` recommendation; return its id.

    ``reorder`` is used so the approved -> applied apply path performs a real
    inventory delta (rather than the no-op ``no_action``/``markdown`` branches),
    exercising the full success transition (Req 11.2).
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=SKU_ID,
        action_kind=ActionKind.REORDER.value,
        rationale="Reorder to cover projected demand.",
        quantity=20,
    )
    return rec.id


def _drive(session: Session, recommendation_id: int, target: str) -> None:
    """Attempt the ``target`` transition through the appropriate service driver.

    Maps a target status onto the real service entry point a reviewer would use:

    * ``approved`` -> :func:`review.approve`
    * ``rejected`` -> :func:`review.reject`
    * ``applied``  -> :func:`apply_action.apply`

    ``review.approve`` / ``review.reject`` raise :class:`IllegalTransition` when
    the current status forbids the move; ``apply_action.apply`` instead returns a
    :class:`ReviewResult` carrying the retained status (it never raises for a
    lifecycle refusal). To give the caller one uniform "was it refused?" signal,
    an apply refusal is normalised here into an :class:`IllegalTransition` raise,
    matching the review-service contract. Either way the stored status is left
    unchanged on refusal.

    Note the one benign self-transition: applying an already-``applied``
    recommendation is an idempotent no-op success (Req 11.4) — the status is
    unchanged but the operation is *not* a refusal, so no exception is raised.
    """
    if target == APPROVED:
        review.approve(session, recommendation_id)
    elif target == REJECTED:
        review.reject(session, recommendation_id)
    elif target == APPLIED:
        result = apply_action.apply(session, recommendation_id)
        # apply signals a lifecycle refusal by returning applied=False with the
        # retained (non-applied) status instead of raising. Normalise to the
        # same IllegalTransition contract the review service uses so both
        # drivers look alike to the property below. An already-applied rec
        # returns applied=True (idempotent no-op) and is therefore not a refusal.
        if not result.applied and result.status.value != APPLIED:
            raise IllegalTransition(current=result.status.value, target=target)
    else:  # pragma: no cover - defensive; targets are constrained by generators
        raise AssertionError(f"unsupported target status: {target!r}")


# --- Generators -------------------------------------------------------------

# The three non-pending statuses a recommendation can already be sitting in when
# a transition is attempted. ``pending`` is the seed state; these are reached by
# driving the legal sequence first.
_NON_PENDING = st.sampled_from([APPROVED, REJECTED, APPLIED])

# The transitions a reviewer can actually *request* through the service layer.
# ``pending`` is the seed/initial state and is never a transition target (there
# is no operation that moves a recommendation *into* pending), so the operable
# targets are exactly approve / reject / apply.
_OPERABLE_TARGET = st.sampled_from([APPROVED, REJECTED, APPLIED])


def _advance_to(session: Session, recommendation_id: int, status: str) -> None:
    """Drive a freshly-seeded pending recommendation to ``status`` legally.

    Uses only legal transitions so the recommendation genuinely reaches the
    requested resting state before the property under test attempts its move:

    * ``pending``  -> no-op (already there)
    * ``approved`` -> approve
    * ``rejected`` -> reject
    * ``applied``  -> approve then apply
    """
    if status == PENDING:
        return
    if status == APPROVED:
        review.approve(session, recommendation_id)
    elif status == REJECTED:
        review.reject(session, recommendation_id)
    elif status == APPLIED:
        review.approve(session, recommendation_id)
        apply_action.apply(session, recommendation_id)
    else:  # pragma: no cover - defensive
        raise AssertionError(f"unsupported resting status: {status!r}")


# --- Property 8 -------------------------------------------------------------


@settings(deadline=None, max_examples=200)
@given(start=_OPERABLE_TARGET, target=_OPERABLE_TARGET)
def test_transition_accepted_iff_legal(start, target):
    """A requested transition takes effect iff ``(start, target)`` is in ``LEGAL``.

    For every ``(start, target)`` pair over the operable targets
    (approve/reject/apply), seed a pending recommendation, advance it legally to
    ``start``, then request ``target`` through the service drivers:

    * if ``(start, target)`` is legal, the transition succeeds and the stored
      status becomes ``target``;
    * otherwise it is refused (:class:`IllegalTransition`) and the stored status
      is retained unchanged (Req 10.4, 12.3).

    The one benign exception is the idempotent re-apply of an already-``applied``
    recommendation (``applied -> applied`` via apply): not in ``LEGAL`` because
    it is not a *state change*, but it is an accepted no-op (Req 11.4) that
    leaves the status at ``applied`` rather than a refusal. It is handled
    explicitly so it does not masquerade as an illegal transition.

    The stored status is a member of the closed set throughout (Req 12.1).

    **Validates: Requirements 10.2, 10.3, 10.4, 12.1, 12.2, 12.3, 11.2**
    """
    with _temp_session() as session:
        rec_id = _seed_pending(session)
        _advance_to(session, rec_id, start)

        # Status is drawn from the closed set at the resting state (Req 12.1).
        current = recommendation_repo.get(session, rec_id).status
        assert current == start
        assert current in CLOSED_STATUSES

        legal = (start, target) in LEGAL
        idempotent_reapply = start == APPLIED and target == APPLIED

        if legal:
            _drive(session, rec_id, target)
            after = recommendation_repo.get(session, rec_id).status
            assert after == target, (
                f"legal transition {start!r} -> {target!r} did not take effect"
            )
            assert after in CLOSED_STATUSES
        elif idempotent_reapply:
            # Re-applying an applied rec is an accepted no-op, not a refusal.
            _drive(session, rec_id, target)
            after = recommendation_repo.get(session, rec_id).status
            assert after == APPLIED
            assert after in CLOSED_STATUSES
        else:
            with pytest.raises(IllegalTransition) as exc_info:
                _drive(session, rec_id, target)
            # The exception carries the (unchanged) current status (Req 10.4).
            assert exc_info.value.current == start
            # Stored status is retained unchanged after the refusal (Req 12.3).
            after = recommendation_repo.get(session, rec_id).status
            assert after == start
            assert after in CLOSED_STATUSES


@settings(deadline=None, max_examples=50)
@given(data=st.data())
def test_full_legal_sequences_persist(data):
    """Driving a full legal sequence persists each status correctly.

    Picks one of the two lifecycle branches from ``pending`` and drives it
    end-to-end through the service layer, asserting the stored status after each
    step:

    * ``pending -> approved -> applied`` (Req 10.2, 11.2), or
    * ``pending -> rejected`` (Req 10.3).

    Every observed status is a member of the closed set (Req 12.1).

    **Validates: Requirements 10.2, 10.3, 11.2, 12.1**
    """
    branch = data.draw(st.sampled_from(["approve_apply", "reject"]))

    with _temp_session() as session:
        rec_id = _seed_pending(session)
        assert recommendation_repo.get(session, rec_id).status == PENDING

        if branch == "approve_apply":
            review.approve(session, rec_id)
            assert recommendation_repo.get(session, rec_id).status == APPROVED

            result = apply_action.apply(session, rec_id)
            assert result.applied is True
            assert result.status == RecommendationStatus.APPLIED
            assert recommendation_repo.get(session, rec_id).status == APPLIED
        else:
            review.reject(session, rec_id)
            assert recommendation_repo.get(session, rec_id).status == REJECTED

        # Final status is a member of the closed set (Req 12.1).
        assert recommendation_repo.get(session, rec_id).status in CLOSED_STATUSES


@settings(deadline=None, max_examples=100)
@given(resting=_NON_PENDING, target=_OPERABLE_TARGET)
def test_terminal_and_non_pending_transitions_refused(resting, target):
    """Transitions out of a non-``pending`` state that aren't legal are refused.

    Advances a seeded recommendation to a non-pending resting state
    (``approved``/``rejected``/``applied``) then requests an operable target.
    Only ``approved -> applied`` is legal from these states; every other move —
    including approve/reject of a non-pending recommendation (Req 10.4) and any
    transition out of the terminal ``rejected``/``applied`` states (Req 12.2) —
    is refused with the stored status retained (Req 12.3). Re-applying an already
    ``applied`` recommendation is an accepted idempotent no-op (Req 11.4), not a
    refusal, and is handled explicitly.

    **Validates: Requirements 10.4, 12.2, 12.3, 12.1**
    """
    with _temp_session() as session:
        rec_id = _seed_pending(session)
        _advance_to(session, rec_id, resting)
        assert recommendation_repo.get(session, rec_id).status == resting

        idempotent_reapply = resting == APPLIED and target == APPLIED

        if (resting, target) in LEGAL:
            # The one legal move from a non-pending state: approved -> applied.
            _drive(session, rec_id, target)
            assert recommendation_repo.get(session, rec_id).status == target
        elif idempotent_reapply:
            # Accepted no-op: status stays applied (Req 11.4), no refusal.
            _drive(session, rec_id, target)
            assert recommendation_repo.get(session, rec_id).status == APPLIED
        else:
            with pytest.raises(IllegalTransition) as exc_info:
                _drive(session, rec_id, target)
            assert exc_info.value.current == resting
            # Status retained unchanged (Req 12.2, 12.3).
            assert recommendation_repo.get(session, rec_id).status == resting
        assert recommendation_repo.get(session, rec_id).status in CLOSED_STATUSES


# --- Focused examples using the shared conftest `session` fixture -----------


def test_approve_then_apply_persists(session):
    """Concrete happy path: pending -> approved -> applied persists correctly.

    **Validates: Requirements 10.2, 11.2, 12.1**
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=SKU_ID,
        action_kind=ActionKind.REORDER.value,
        rationale="Reorder.",
        quantity=20,
    )

    review.approve(session, rec.id)
    assert recommendation_repo.get(session, rec.id).status == APPROVED

    result = apply_action.apply(session, rec.id)
    assert result.applied is True
    assert recommendation_repo.get(session, rec.id).status == APPLIED


def test_reject_persists(session):
    """Concrete path: pending -> rejected persists correctly.

    **Validates: Requirements 10.3, 12.1**
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=SKU_ID,
        action_kind=ActionKind.NO_ACTION.value,
        rationale="No change warranted.",
    )

    review.reject(session, rec.id)
    assert recommendation_repo.get(session, rec.id).status == REJECTED


def test_approve_non_pending_refused_status_retained(session):
    """Approving an already-rejected recommendation is refused; status retained.

    **Validates: Requirements 10.4, 12.2, 12.3**
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=SKU_ID,
        action_kind=ActionKind.NO_ACTION.value,
        rationale="No change.",
    )

    review.reject(session, rec.id)
    assert recommendation_repo.get(session, rec.id).status == REJECTED

    with pytest.raises(IllegalTransition) as exc_info:
        review.approve(session, rec.id)
    assert exc_info.value.current == REJECTED
    # Rejected status is retained unchanged.
    assert recommendation_repo.get(session, rec.id).status == REJECTED


def test_apply_non_approved_refused_status_retained(session):
    """Applying a pending recommendation is refused; status retained as pending.

    ``apply_action.apply`` signals the refusal by returning ``applied=False``
    with the current (pending) status rather than raising.

    **Validates: Requirements 11.2, 12.3**
    """
    _seed_sku(session)
    case = case_repo.create(session, sku=SKU_ID, status="complete")
    rec = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=SKU_ID,
        action_kind=ActionKind.REORDER.value,
        rationale="Reorder.",
        quantity=20,
    )

    result = apply_action.apply(session, rec.id)
    assert result.applied is False
    assert result.status == RecommendationStatus.PENDING
    # Status retained unchanged.
    assert recommendation_repo.get(session, rec.id).status == PENDING
