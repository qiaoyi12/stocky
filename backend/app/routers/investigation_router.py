"""Investigation router — run a case and read the latest case for a SKU.

Exposes the two investigation endpoints from the design's API table:

* ``POST /api/investigations/{sku}/run`` — kick off the fixed four-agent
  sequence for one SKU and return the resulting case id + status (Req 4.1, 8.1,
  15.1).
* ``GET /api/cases/{sku}`` — return the latest investigation case for a SKU:
  the four agent outputs, the case status/failed step, and the associated
  recommendation when one exists (Req 15.1, 15.2).

This router sits on the read-only side of the safety boundary. It builds a
plain-data :class:`~app.agents.context.SkuSnapshot` from the stored SKU row and
hands it to the orchestrator; it never imports the ``sku_repo`` write functions
or the apply-action service. The single ``session.commit()`` after a run
persists only the case + pending recommendation the orchestrator created —
inventory rows are untouched.

Requirements: 4.1, 8.1, 15.1, 15.2.
"""

from __future__ import annotations

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agents.context import SkuSnapshot
from app.agents.llm_client import LLMClient
from app.agents.orchestrator import run_investigation
from app.db import get_session
from app.models import Case, Recommendation, Sku
from app.repositories import case_repo, recommendation_repo, sku_repo
from app.schemas import (
    CaseResponse,
    CaseStatus,
    RecommendationResponse,
    RunInvestigationResponse,
)

router = APIRouter(tags=["investigation"])


def _parse_classifications(raw: object) -> List[str]:
    """Parse the SKU's cached ``classifications`` JSON string into a label list.

    The column stores a JSON array of strings (default ``"[]"``). Falls back to
    an empty list for a null/blank/malformed value so a partly-populated row can
    still be investigated.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(label) for label in raw]
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(label) for label in parsed]
    return []


def _snapshot_from_row(row: Sku, sales_history: List[int]) -> SkuSnapshot:
    """Build an immutable, plain-data :class:`SkuSnapshot` from a stored SKU row.

    Copies the source fields plus the cached deterministic metrics, translating
    the integer ``no_recent_sales`` flag to a bool and the JSON
    ``classifications`` string to a list. The snapshot holds no session, so it
    grants the agents no write path back to the store.
    """
    classifications = _parse_classifications(row.classifications)
    return SkuSnapshot(
        sku=row.sku,
        name=row.name,
        category=row.category,
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        unit_cost=row.unit_cost,
        selling_price=row.selling_price,
        supplier_name=row.supplier_name,
        avg_daily_sales=row.avg_daily_sales,
        last_sold_date=row.last_sold_date,
        sales_history=list(sales_history),
        sales_velocity=row.sales_velocity,
        days_of_cover=row.days_of_cover,
        stockout_eta=row.stockout_eta,
        no_recent_sales=bool(row.no_recent_sales),
        classifications=classifications,
    )


def _recommendation_to_response(rec: Recommendation) -> RecommendationResponse:
    """Map a stored ``Recommendation`` row to its API response shape."""
    return RecommendationResponse(
        id=rec.id,
        case_id=rec.case_id,
        sku=rec.sku,
        action_kind=rec.action_kind,
        quantity=rec.quantity,
        new_reorder_point=rec.new_reorder_point,
        rationale=rec.rationale,
        status=rec.status,
        created_at=rec.created_at,
        decided_at=rec.decided_at,
        applied_at=rec.applied_at,
    )


def _case_to_response(case: Case, session: Session) -> CaseResponse:
    """Map a stored ``Case`` row to :class:`CaseResponse`, attaching its recommendation.

    Presents each agent's output text (``None`` for steps that did not run), the
    case status and failed step, and the associated recommendation (if any)
    fetched via ``recommendation_repo`` and mapped to
    :class:`RecommendationResponse` (Req 15.1, 15.2).
    """
    rec = recommendation_repo.get_for_case(session, case.id)
    return CaseResponse(
        id=case.id,
        sku=case.sku,
        status=CaseStatus(case.status),
        failed_step=case.failed_step,
        detective_out=case.detective_out,
        forecast_out=case.forecast_out,
        strategy_out=case.strategy_out,
        manager_out=case.manager_out,
        created_at=case.created_at,
        completed_at=case.completed_at,
        recommendation=_recommendation_to_response(rec) if rec is not None else None,
    )


@router.post("/api/investigations/{sku}/run", response_model=RunInvestigationResponse)
def run_investigation_endpoint(
    sku: str, session: Session = Depends(get_session)
) -> RunInvestigationResponse:
    """Run the four-agent investigation for ``sku`` and return case id + status.

    Loads the stored SKU (404 if unknown), builds a plain-data snapshot from its
    cached metrics + classifications and its sales history, then runs the fixed
    Detective→Forecast→Strategy→Manager sequence via the orchestrator. On success
    a ``complete`` case plus one ``pending`` recommendation are persisted; on an
    LLM failure the case is recorded as ``error`` with the failed step. The
    single commit saves only case/recommendation rows — never inventory
    (Req 4.1, 8.1, 15.1).
    """
    row = sku_repo.get(session, sku)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown sku: {sku}")

    sales_history = sku_repo.get_sales_units(session, sku)
    snapshot = _snapshot_from_row(row, sales_history)

    result = run_investigation(
        session,
        snapshot,
        snapshot.classifications,
        client=LLMClient(),
    )
    session.commit()

    return RunInvestigationResponse(
        case_id=result.case_id,
        status=CaseStatus(result.status),
        failed_step=result.failed_step,
    )


@router.get("/api/cases/{sku}", response_model=CaseResponse)
def get_case_endpoint(
    sku: str, session: Session = Depends(get_session)
) -> CaseResponse:
    """Return the latest investigation case for ``sku`` (404 if none exists).

    Presents the four agent outputs, the case status/failed step, and the
    associated recommendation + its lifecycle status when present (Req 15.1,
    15.2).
    """
    case = case_repo.get_latest_for_sku(session, sku)
    if case is None:
        raise HTTPException(status_code=404, detail=f"no case for sku: {sku}")
    return _case_to_response(case, session)
