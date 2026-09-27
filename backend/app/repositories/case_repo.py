"""Case repository — agent investigation runs and their four step outputs.

Wraps writes/reads for the ``cases`` table. The orchestrator is allowed to
import this module: creating a case and recording agent output *text* is not an
inventory mutation (Req 9.1, 9.2). All agent output lands in plain ``*_out``
text columns as inert data — never executed.

Case status values: ``running`` | ``complete`` | ``error``.
Step names:          ``detective`` | ``forecast`` | ``strategy`` | ``manager``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Case

# The four output columns keyed by step name (the fixed orchestration order).
_STEP_COLUMNS = {
    "detective": "detective_out",
    "forecast": "forecast_out",
    "strategy": "strategy_out",
    "manager": "manager_out",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create(session: Session, *, sku: str, status: str = "running") -> Case:
    """Create a new investigation case for ``sku`` (default status ``running``)."""
    case = Case(sku=sku, status=status, created_at=_now_iso())
    session.add(case)
    session.flush()
    return case


def save_step(session: Session, *, case_id: int, step: str, text: str) -> Case:
    """Record one agent step's output text on the case.

    ``step`` must be one of the four known step names; its output is stored in
    the matching ``*_out`` column as inert text. Raises ``ValueError`` for an
    unknown step and ``KeyError`` for an unknown case.
    """
    column = _STEP_COLUMNS.get(step)
    if column is None:
        raise ValueError(f"unknown step: {step!r}")
    case = session.get(Case, case_id)
    if case is None:
        raise KeyError(f"unknown case id: {case_id!r}")
    setattr(case, column, text)
    session.flush()
    return case


def mark_failed(session: Session, *, case_id: int, step: str, error: Optional[str] = None) -> Case:
    """Mark a case as failed at ``step`` (fail-stop on an LLM error).

    Sets status ``error`` and records the failed step name. The optional
    ``error`` message is preserved as inert text in the failed step's output
    column when that column is known. Raises ``KeyError`` for an unknown case.
    """
    case = session.get(Case, case_id)
    if case is None:
        raise KeyError(f"unknown case id: {case_id!r}")
    case.status = "error"
    case.failed_step = step
    case.completed_at = _now_iso()
    column = _STEP_COLUMNS.get(step)
    if column is not None and error is not None:
        setattr(case, column, error)
    session.flush()
    return case


def mark_complete(session: Session, *, case_id: int) -> Case:
    """Mark a case as successfully completed (all four steps ran)."""
    case = session.get(Case, case_id)
    if case is None:
        raise KeyError(f"unknown case id: {case_id!r}")
    case.status = "complete"
    case.completed_at = _now_iso()
    session.flush()
    return case


def get(session: Session, case_id: int) -> Optional[Case]:
    """Return the case by id, or ``None`` if it does not exist."""
    return session.get(Case, case_id)


def get_latest_for_sku(session: Session, sku: str) -> Optional[Case]:
    """Return the most recent case for ``sku``, or ``None`` if none exists.

    Backs ``GET /api/cases/{sku}``, which shows the latest investigation.
    """
    return session.scalars(
        select(Case).where(Case.sku == sku).order_by(Case.id.desc()).limit(1)
    ).first()
