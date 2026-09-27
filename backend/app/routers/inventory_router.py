"""Inventory API router for STOCKY (Requirement 14).

Exposes the two read-only inventory endpoints from the design's API table:

* ``GET /api/inventory`` — every SKU with its cached deterministic metrics and
  condition classifications, wrapped in an
  :class:`~app.schemas.InventoryListResponse` (Req 14.1, 14.2).
* ``GET /api/inventory/{sku}`` — a single SKU's detail, or ``404`` when the SKU
  is unknown (Req 14.3).

Both endpoints project stored ``skus`` rows onto the
:class:`~app.schemas.InventoryItem` API shape via
:func:`app.services.dashboard.sku_to_item`, reusing the identical mapping the
dashboard aggregation uses (it parses the ``classifications`` JSON array and
converts the integer ``no_recent_sales`` flag to a bool). Sharing that helper
keeps the row -> item projection consistent across every read endpoint.

This router is on the read-only side of the safety boundary: it goes only
through the ``sku_repo`` read functions and the read-only projection helper,
never importing an inventory-write function or the apply-action service. It
performs no LLM calls, mutates nothing, and issues no commit.

Note on routing: full paths (rather than an ``/api/inventory`` prefix) are used
so this router's ``GET /api/inventory`` and ``GET /api/inventory/{sku}`` sit
alongside the ingest router's ``POST /api/inventory/upload`` without a prefix
collision — the literal ``/upload`` POST and the ``{sku}`` GET do not conflict.

Requirements: 14.1, 14.2, 14.3.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.repositories import sku_repo
from app.schemas import InventoryItem, InventoryListResponse
from app.services.dashboard import sku_to_item

router = APIRouter(tags=["inventory"])


@router.get("/api/inventory", response_model=InventoryListResponse)
def list_inventory(session: Session = Depends(get_session)) -> InventoryListResponse:
    """Return every SKU with its metrics and classifications (Req 14.1, 14.2).

    Reads all SKU rows through :func:`sku_repo.list_all` (deterministic id
    order) and projects each onto :class:`~app.schemas.InventoryItem` via
    :func:`app.services.dashboard.sku_to_item`, so every item carries its cached
    Sales_Velocity / Days_Of_Cover metrics and parsed condition classifications.
    Read-only: no mutation, no LLM.
    """
    items = [sku_to_item(row) for row in sku_repo.list_all(session)]
    return InventoryListResponse(items=items)


@router.get("/api/inventory/{sku}", response_model=InventoryItem)
def get_inventory_item(
    sku: str, session: Session = Depends(get_session)
) -> InventoryItem:
    """Return one SKU's detail, or ``404`` if unknown (Req 14.3).

    Looks up the SKU through :func:`sku_repo.get`; when no row exists a
    ``404 Not Found`` is raised. Otherwise the row is projected onto
    :class:`~app.schemas.InventoryItem` via
    :func:`app.services.dashboard.sku_to_item` (same mapping as the listing).
    Read-only: no mutation, no LLM.
    """
    row = sku_repo.get(session, sku)
    if row is None:
        raise HTTPException(status_code=404, detail=f"unknown sku: {sku}")
    return sku_to_item(row)
