
"""Recommendation repository — Manager proposals and their lifecycle status.

Wraps writes/reads for the ``recommendations`` table. The orchestrator imports
this to persist exactly one ``pending`` recommendation per successful case
(Req 7.3) — a recommendation is a proposal, not inventory state, so this is not
an inventory mutation and does not cross the safety boundary (design "Safety
Boundary").

Lifecycle status transitions themselves are validated centrally in
``services/review.py``; this module only reads and writes the ``status`` field
plus its timestamps. It never mutates ``skus`` / ``sales_history``.

Status values: ``pending`` | ``approved`` | ``rejected`` | ``applied`` (Req 10-12).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recommendation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_pending(
    session: Session,
    *,
    case_id: int,
    sku: str,
    action_kind: str,
    rationale: str,
    quantity: Optional[int] = None,
    new_reorder_point: Optional[int] = None,
) -> Recommendation:
    """Persist a new recommendation with status ``pending`` (Req 7.3).

    ``action_kind`` is one of the closed action kinds
    (``reorder``/``adjust_reorder``/``markdown``/``no_action``); ``quantity``
    and ``new_reorder_point`` are populated per kind. ``rationale`` is the
    Manager's supporting text, stored as inert text only (Req 7.2, 9.4).
    """
    rec = Recommendation(
        case_id=case_id,
        sku=sku,
        action_kind=action_kind,
        quantity=quantity,
        new_reorder_point=new_reorder_point,
        rationale=rationale,
        status="pending",
        created_at=_now_iso(),
    )
    session.add(rec)
    session.flush()
    return rec


def get(session: Session, recommendation_id: int) -> Optional[Recommendation]:
    """Return the recommendation by id, or ``None`` if it does not exist."""
    return session.get(Recommendation, recommendation_id)


def get_for_case(session: Session, case_id: int) -> Optional[Recommendation]:
    """Return the recommendation attached to ``case_id``, if any."""
    return session.scalars(
        select(Recommendation)
        .where(Recommendation.case_id == case_id)
        .order_by(Recommendation.id.desc())
        .limit(1)
    ).first()


def list_all(session: Session) -> List[Recommendation]:
    """Return every recommendation, ordered by id (for aggregation/impact)."""
    return list(session.scalars(select(Recommendation).order_by(Recommendation.id)))


def set_status(
    session: Session,
    *,
    recommendation_id: int,
    status: str,
    decided: bool = False,
    applied: bool = False,
) -> Recommendation:
    """Update a recommendation's lifecycle ``status`` and matching timestamp.

    ``decided`` stamps ``decided_at`` (approve/reject); ``applied`` stamps
    ``applied_at`` (deterministic apply). Transition legality is enforced by the
    review service before this is called. Raises ``KeyError`` if the
    recommendation does not exist.
    """
    rec = session.get(Recommendation, recommendation_id)
    if rec is None:
        raise KeyError(f"unknown recommendation id: {recommendation_id!r}")
    rec.status = status
    if decided:
        rec.decided_at = _now_iso()
    if applied:
        rec.applied_at = _now_iso()
    session.flush()
    return rec
