"""Impact API router for STOCKY (Requirement 17).

Exposes ``GET /api/impact``: the read-only aggregation endpoint that powers the
Impact page. It is a thin HTTP seam over
:func:`app.services.dashboard.build_impact`, which produces the two Impact
summaries — recommendation outcome counts by lifecycle status across the dataset
(Req 17.1) and aggregate per-condition SKU counts across all SKUs (Req 17.2),
reusing the Dashboard aggregation helpers so both pages count identically.

This router sits firmly on the read-only side of the safety boundary: it goes
only through the aggregation service (which itself uses the ``sku_repo`` /
``recommendation_repo`` read paths) and never imports an inventory-write
function or the apply-action service. It performs no LLM calls and mutates
nothing, so no commit is issued.

Requirements: 17.1, 17.2.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import ImpactResponse
from app.services.dashboard import build_impact

router = APIRouter(tags=["impact"])


@router.get("/api/impact", response_model=ImpactResponse)
def get_impact(session: Session = Depends(get_session)) -> ImpactResponse:
    """Return the aggregated Impact summary (Req 17.1, 17.2).

    Delegates to :func:`app.services.dashboard.build_impact`, which reads every
    SKU and recommendation through the repository read paths and returns the
    recommendation outcome counts by lifecycle status and the aggregate
    per-condition SKU counts. Read-only: no inventory mutation, no LLM, no
    commit.
    """
    return build_impact(session)
