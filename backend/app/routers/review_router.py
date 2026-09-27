"""Review / apply router — the human reviewer's control surface over the lifecycle.

Exposes the three review endpoints from the design's API table:

* ``POST /api/recommendations/{id}/approve`` — approve a pending recommendation
  (``pending -> approved``) and then immediately invoke the deterministic
  apply-action service. Returns the :class:`~app.schemas.ReviewResult` from
  apply: on a successful write the status is ``applied`` (Req 11.2); on a write
  failure it stays ``approved`` with an ``error`` (Req 11.3). (Req 10.2, 11.1.)
* ``POST /api/recommendations/{id}/reject`` — reject a pending recommendation
  (``pending -> rejected``); returns ``ReviewResult(status=rejected,
  applied=False)`` (Req 10.3).
* ``POST /api/recommendations/{id}/apply`` — deterministically apply an already
  ``approved`` recommendation (also called internally by ``approve``).
  Returns the :class:`~app.schemas.ReviewResult` (Req 11.1-11.4).

Error handling (design "Error Handling"):

* Unknown recommendation id -> HTTP 404. This is a routing/programming error,
  not a lifecycle refusal, so it is surfaced as an HTTP error rather than a
  ``ReviewResult``.
* Approve/reject a non-pending recommendation -> refused with the *current*
  status returned unchanged (Req 10.4, 12.3). Consistent with the apply gate
  (which returns a ``ReviewResult`` carrying the current status on refusal),
  this handler returns a ``ReviewResult`` with the retained status and an
  ``error`` describing the refusal — no exception escapes and nothing is
  committed for that recommendation.

Safety boundary: this router performs no inventory writes itself. Every status
change flows through ``services/review.py`` (lifecycle validation) and every
inventory mutation flows through ``services/apply_action.py`` (the sole
post-approval mutator). The router only owns the HTTP seam and the request-scoped
transaction boundary: it commits once after a successful mutation and rolls back
on any unexpected persistence error.

Requirements: 10.2, 10.3, 11.1, 11.2, 11.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import RecommendationStatus, ReviewResult
from app.services import apply_action, review
from app.services.review import IllegalTransition, UnknownRecommendation

router = APIRouter(prefix="/api/recommendations", tags=["review"])


@router.post("/{recommendation_id}/approve", response_model=ReviewResult)
def approve_recommendation(
    recommendation_id: int, session: Session = Depends(get_session)
) -> ReviewResult:
    """Approve a pending recommendation, then deterministically apply it.

    Runs the two-step approve-on-review flow (design API table; Req 10.2, 11.1):

    1. ``review.approve`` validates and performs ``pending -> approved``. Approving
       a non-pending recommendation is refused with its current status retained
       (Req 10.4) and returned as a :class:`ReviewResult` — nothing is committed.
    2. ``apply_action.apply`` deterministically applies the now-approved action.
       On a successful inventory write the status becomes ``applied`` (Req 11.2);
       on a write failure it stays ``approved`` and the returned result carries an
       ``error`` (Req 11.3).

    The single ``session.commit()`` persists the approve transition plus whatever
    apply produced (an ``applied`` status + inventory delta, or an ``approved``
    status left intact on failure). Returns the ``ReviewResult`` from apply.
    Raises HTTP 404 if the id does not resolve.
    """
    try:
        review.approve(session, recommendation_id)
    except UnknownRecommendation:
        raise HTTPException(
            status_code=404, detail=f"unknown recommendation id: {recommendation_id}"
        )
    except IllegalTransition as exc:
        # Refuse (Req 10.4, 12.3): retain the current status, mutate nothing.
        # Mirror the apply gate's shape — return the current status + an error
        # rather than raising, so the reviewer sees the unchanged lifecycle state.
        session.rollback()
        return ReviewResult(
            status=RecommendationStatus(exc.current),
            applied=False,
            error=(
                f"cannot approve recommendation in status {exc.current!r}; "
                "must be 'pending'"
            ),
        )

    # Approved: now deterministically apply. apply_action owns the gate and the
    # sole inventory-write path; it returns a ReviewResult (never raises for a
    # lifecycle/apply failure — only UnknownRecommendation, which cannot happen
    # here since approve just resolved the id).
    try:
        result = apply_action.apply(session, recommendation_id)
        session.commit()
    except Exception:
        session.rollback()
        raise

    return result


@router.post("/{recommendation_id}/reject", response_model=ReviewResult)
def reject_recommendation(
    recommendation_id: int, session: Session = Depends(get_session)
) -> ReviewResult:
    """Reject a pending recommendation: ``pending -> rejected`` (Req 10.3).

    ``review.reject`` validates and performs the transition. Rejecting a
    non-pending recommendation is refused with its current status retained
    (Req 10.4, 12.3) and returned as a :class:`ReviewResult` with an ``error`` —
    nothing is committed. On success the status becomes ``rejected`` and the
    single commit persists it; returns ``ReviewResult(status=rejected,
    applied=False)``. No inventory is ever touched by a reject. Raises HTTP 404
    if the id does not resolve.
    """
    try:
        rec = review.reject(session, recommendation_id)
        session.commit()
    except UnknownRecommendation:
        session.rollback()
        raise HTTPException(
            status_code=404, detail=f"unknown recommendation id: {recommendation_id}"
        )
    except IllegalTransition as exc:
        session.rollback()
        return ReviewResult(
            status=RecommendationStatus(exc.current),
            applied=False,
            error=(
                f"cannot reject recommendation in status {exc.current!r}; "
                "must be 'pending'"
            ),
        )

    return ReviewResult(status=RecommendationStatus(rec.status), applied=False)


@router.post("/{recommendation_id}/apply", response_model=ReviewResult)
def apply_recommendation(
    recommendation_id: int, session: Session = Depends(get_session)
) -> ReviewResult:
    """Deterministically apply an approved recommendation (Req 11.1-11.4).

    Delegates straight to ``apply_action.apply``, the sole post-approval
    inventory mutator. It is gated (only an ``approved`` recommendation mutates
    inventory; a non-approved status is refused with the current status
    returned, Req 11.1) and idempotent (re-applying an ``applied`` recommendation
    is a no-op success, Req 11.4). On a write failure the status stays
    ``approved`` and the result carries an ``error`` (Req 11.3).

    The single commit persists the applied status + inventory delta on success
    (or leaves the ``approved`` status intact on a handled apply failure).
    Returns the :class:`ReviewResult`. Raises HTTP 404 if the id does not
    resolve.
    """
    try:
        result = apply_action.apply(session, recommendation_id)
        session.commit()
    except UnknownRecommendation:
        session.rollback()
        raise HTTPException(
            status_code=404, detail=f"unknown recommendation id: {recommendation_id}"
        )
    except Exception:
        session.rollback()
        raise

    return result
