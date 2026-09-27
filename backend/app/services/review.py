"""Recommendation lifecycle review service (approve / reject + transition rules).

This module centralizes recommendation lifecycle validation for STOCKY. It owns
the single legal-transition table and the ``transition`` guard; every status
change flows through here so the lifecycle order can be enforced in one place
(design "Recommendation Lifecycle"; Req 12.1, 12.2, 12.3).

Legal transitions (the closed set):

* ``pending  -> approved``   (Reviewer approves; Req 10.2)
* ``pending  -> rejected``   (Reviewer rejects; Req 10.3)
* ``approved -> applied``    (deterministic apply-on-approval; Req 11.1, 11.2)

Anything else — including approving or rejecting a recommendation that is not
``pending`` — is refused with ``IllegalTransition`` carrying the current status,
and the stored status is retained unchanged (Req 10.4, 12.3).

Scope boundary: this service is lifecycle-only. It does NOT perform the database
inventory apply — that belongs to ``services/apply_action.py`` (task 6.2), the
sole inventory mutator. ``approve`` therefore stops at status ``approved`` and
returns the approved recommendation so the router can then invoke apply. This
module deliberately does not import ``sku_repo`` write functions or
``apply_action`` (design "Safety Boundary": only one writer).
"""

from __future__ import annotations

from typing import Set, Tuple

from sqlalchemy.orm import Session

from app.models import Recommendation
from app.repositories import recommendation_repo
from app.schemas import RecommendationStatus

# --- Legal transition table -------------------------------------------------

# The closed set of allowed (current_status, target_status) pairs. Any pair not
# in this set is an illegal transition (Req 12.3). Values are the lifecycle
# status strings persisted on ``Recommendation.status``.
LEGAL: Set[Tuple[str, str]] = {
    (RecommendationStatus.PENDING.value, RecommendationStatus.APPROVED.value),
    (RecommendationStatus.PENDING.value, RecommendationStatus.REJECTED.value),
    (RecommendationStatus.APPROVED.value, RecommendationStatus.APPLIED.value),
}


class IllegalTransition(Exception):
    """Raised when a requested lifecycle transition is not in ``LEGAL``.

    Carries the recommendation's ``current`` status so callers can surface it
    unchanged (Req 10.4, 12.3). Raising this before any write means the stored
    status is retained.
    """

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"illegal lifecycle transition {current!r} -> {target!r}"
        )


class UnknownRecommendation(Exception):
    """Raised when a recommendation id does not resolve to a stored row."""

    def __init__(self, recommendation_id: int) -> None:
        self.recommendation_id = recommendation_id
        super().__init__(f"unknown recommendation id: {recommendation_id!r}")


def transition(session: Session, recommendation: Recommendation, target: str) -> None:
    """Validate a lifecycle transition for ``recommendation`` to ``target``.

    Checks ``(recommendation.status, target)`` against ``LEGAL``. If the pair is
    not allowed, raises ``IllegalTransition`` carrying the current status and
    performs no write, so the stored status is retained (Req 12.3, 10.4).

    This is a pure guard: it does not mutate the recommendation. Callers apply
    the status change (via ``recommendation_repo.set_status``) only after this
    returns without raising.
    """
    if (recommendation.status, target) not in LEGAL:
        raise IllegalTransition(current=recommendation.status, target=target)


def _require(session: Session, recommendation_id: int) -> Recommendation:
    rec = recommendation_repo.get(session, recommendation_id)
    if rec is None:
        raise UnknownRecommendation(recommendation_id)
    return rec


def approve(session: Session, recommendation_id: int) -> Recommendation:
    """Approve a pending recommendation: ``pending -> approved`` (Req 10.2).

    Validates the transition first; approving a non-pending recommendation is
    refused with ``IllegalTransition`` carrying the current status, leaving the
    stored status unchanged (Req 10.4). On success, stamps ``decided_at`` and
    returns the approved recommendation.

    Note: this stops at ``approved`` and does NOT apply the inventory action.
    The router invokes ``apply_action.apply()`` (task 6.2) afterward, using the
    returned recommendation as the seam.
    """
    rec = _require(session, recommendation_id)
    transition(session, rec, RecommendationStatus.APPROVED.value)
    return recommendation_repo.set_status(
        session,
        recommendation_id=recommendation_id,
        status=RecommendationStatus.APPROVED.value,
        decided=True,
    )


def reject(session: Session, recommendation_id: int) -> Recommendation:
    """Reject a pending recommendation: ``pending -> rejected`` (Req 10.3).

    Validates the transition first; rejecting a non-pending recommendation is
    refused with ``IllegalTransition`` carrying the current status, leaving the
    stored status unchanged (Req 10.4). On success, stamps ``decided_at`` and
    returns the rejected recommendation.
    """
    rec = _require(session, recommendation_id)
    transition(session, rec, RecommendationStatus.REJECTED.value)
    return recommendation_repo.set_status(
        session,
        recommendation_id=recommendation_id,
        status=RecommendationStatus.REJECTED.value,
        decided=True,
    )
