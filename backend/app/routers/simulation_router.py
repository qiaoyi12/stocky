"""Simulation router — the What-If Simulation_Engine's HTTP seam (Requirement 16).

Exposes the single simulation endpoint from the design's API table:

* ``POST /api/simulation/{sku}`` — project the effect of hypothetical inputs for
  one SKU and return the current vs projected metrics side by side (Req 16.1,
  16.3).

The request body is a :class:`~app.schemas.SimulationRequest` of optional
overrides; only the provided fields override the stored SKU snapshot. The
handler delegates to :func:`app.services.simulation.simulate`, which builds a
throwaway in-memory snapshot, recomputes with the same pure Detection_Engine
functions used at ingest, and returns a :class:`~app.schemas.SimulationResponse`.

Safety boundary: this endpoint is strictly read-only (Req 16.2). The service
reads the stored SKU through the ``sku_repo`` read functions only and never
opens a write path, so the router owns no transaction boundary — there is
nothing to commit. An unknown SKU surfaces from the service as ``KeyError`` and
is translated here into an HTTP 404, mirroring the routing/programming-error
handling in the other routers.

Requirements: 16.1, 16.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.schemas import SimulationRequest, SimulationResponse
from app.services import simulation

router = APIRouter(tags=["simulation"])


@router.post("/api/simulation/{sku}", response_model=SimulationResponse)
def simulate_sku(
    sku: str,
    body: SimulationRequest,
    session: Session = Depends(get_session),
) -> SimulationResponse:
    """Project hypothetical inputs for ``sku`` and return current vs projected views.

    Delegates to :func:`app.services.simulation.simulate`, which overlays only
    the provided override fields onto an in-memory copy of the stored SKU and
    recomputes metrics + classifications with the same deterministic engine used
    at ingest (Req 16.1). The response carries both the ``current`` and
    ``projected`` :class:`~app.schemas.MetricsView` values so they can be
    compared side by side (Req 16.3).

    The call is read-only (Req 16.2): the service never writes and nothing is
    committed. An unknown SKU is raised as ``KeyError`` by the service and
    translated here into an HTTP 404.
    """
    try:
        return simulation.simulate(session, sku, body)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown sku: {sku}")
