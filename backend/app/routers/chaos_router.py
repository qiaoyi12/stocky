"""Chaos Mode API router for STOCKY (Requirement 18, NICE TO HAVE).

Exposes ``POST /api/chaos``: the entry point for the Chaos Mode page. It is a
thin HTTP seam over :func:`app.services.chaos.apply_chaos`, which injects a
synthetic disruptive event (demand spike / demand drop / supply delay) onto an
in-memory working copy of the affected SKUs and returns their reclassified
before/after projection (Req 18.1, 18.2).

This router sits firmly on the read-only side of the safety boundary: the chaos
service reads the canonical dataset only through the ``sku_repo`` read path and
writes nothing, so the originally uploaded source data is never altered
(Req 18.3). No inventory-write function is imported, no LLM is called, and no
commit is issued.

Requirements: 18.1, 18.2, 18.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.services.chaos import ChaosRequest, ChaosResponse, apply_chaos

router = APIRouter(tags=["chaos"])


@router.post("/api/chaos", response_model=ChaosResponse)
def inject_chaos(
    request: ChaosRequest,
    session: Session = Depends(get_session),
) -> ChaosResponse:
    """Inject a synthetic disruptive event and return the reclassification (Req 18.1-18.3).

    Delegates to :func:`app.services.chaos.apply_chaos`, which builds an
    in-memory working copy of the affected SKUs, applies the requested event,
    reruns the Detection_Engine on the working copy, and returns the per-SKU
    before/after result. Read-only with respect to the source dataset: no
    inventory mutation, no LLM, no commit (Req 18.3).

    Args:
        request: The :class:`~app.services.chaos.ChaosRequest` body describing
            the scenario, intensity, and optional target SKUs.
        session: Request-scoped SQLAlchemy session (used for reads only).

    Returns:
        The :class:`~app.services.chaos.ChaosResponse` with per-SKU before/after
        classifications and key metrics for the working copy.
    """
    try:
        return apply_chaos(session, request)
    except KeyError as exc:
        # An explicitly requested SKU id does not exist.
        raise HTTPException(status_code=404, detail=str(exc)) from exc
