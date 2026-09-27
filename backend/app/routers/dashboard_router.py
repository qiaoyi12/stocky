"""Dashboard API router for STOCKY (Requirement 13).

Exposes ``GET /api/dashboard``: the read-only aggregation endpoint that powers
the Dashboard page. It is a thin HTTP seam over
:func:`app.services.dashboard.build_dashboard`, which produces the three
Dashboard summaries — per-condition SKU counts (Req 13.1), per-lifecycle-status
recommendation counts (Req 13.2), and the prioritised stockout-risk group
(Req 13.3).

This router sits firmly on the read-only side of the safety boundary: it goes
only through the aggregation service (which itself uses the ``sku_repo`` /
``recommendation_repo`` read paths) and never imports an inventory-write
function or the apply-action service. It performs no LLM calls and mutates
nothing, so no commit is issued.

Requirements: 13.1, 13.2, 13.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import DashboardResponse
from app.services.dashboard import build_dashboard

router = APIRouter(tags=["dashboard"])


@router.get("/api/dashboard", response_model=DashboardResponse)
def get_dashboard(session: Session = Depends(get_session)) -> DashboardResponse:
    """Return the aggregated Dashboard summary (Req 13.1-13.3).

    Delegates to :func:`app.services.dashboard.build_dashboard`, which reads
    every SKU and recommendation through the repository read paths and returns
    the per-condition counts, per-lifecycle-status counts, and prioritised
    stockout-risk group. Read-only: no inventory mutation, no LLM, no commit.
    """
    return build_dashboard(session)
