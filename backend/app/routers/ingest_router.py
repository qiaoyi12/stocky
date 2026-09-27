"""Ingest API router for STOCKY (Requirements 1.1, 1.5).

Exposes ``POST /api/inventory/upload``: the multipart CSV entry point that ties
the deterministic ingest halves together. It reads the uploaded file bytes,
hands them to the pure :func:`app.ingest.csv_parser.parse_csv` validator, then
persists the accepted rows through the deterministic
:func:`app.ingest.loader.load_rows` writer inside a request-scoped session, and
returns the :class:`~app.schemas.UploadResult` (accepted / rejected counts,
per-row reasons, and a missing-column name when the whole upload was rejected).

This router is a thin HTTP seam only: all validation lives in the parser and all
persistence lives in the loader (which goes exclusively through the sanctioned
``sku_repo`` inventory-write path). The router itself contains no business logic
and never touches an LLM.

Transaction boundary: the loader flushes through the repository writers but the
caller owns commit/rollback, so this handler commits once after a successful
load and rolls back on any persistence error. When the whole upload is rejected
for a missing column, no rows are accepted and nothing is written.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db import get_session
from app.ingest.csv_parser import parse_csv
from app.ingest.loader import load_rows
from app.repositories import upload_repo
from app.schemas import UploadResult

router = APIRouter(prefix="/api/inventory", tags=["inventory"])


@router.post("/upload", response_model=UploadResult)
async def upload_inventory_csv(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> UploadResult:
    """Upload an inventory CSV and persist the accepted rows (Req 1.1, 1.5).

    Reads the uploaded file's bytes, validates and parses them with
    :func:`~app.ingest.csv_parser.parse_csv`, and — when at least one row was
    accepted — persists those rows deterministically via
    :func:`~app.ingest.loader.load_rows`, committing once on success. Also
    logs the upload (filename + accepted sku count) via
    :func:`~app.repositories.upload_repo.create` so it shows up in the upload
    history.

    Args:
        file: The multipart-uploaded CSV file. Its raw bytes are passed straight
            to the parser, which decodes UTF-8 (BOM tolerated).
        session: Request-scoped SQLAlchemy session provided by
            :func:`app.db.get_session`; this handler owns its commit/rollback.

    Returns:
        The :class:`~app.schemas.UploadResult` describing accepted count,
        rejected count, per-row rejection reasons, and the missing-column name
        when the entire upload was rejected (Req 1.3, 1.4, 1.5).
    """
    content = await file.read()
    rows, result = parse_csv(content)

    if rows:
        try:
            load_rows(session, rows)
            upload_repo.create(session, filename=file.filename, sku_count=result.accepted)
            session.commit()
        except Exception:
            session.rollback()
            raise

    return result
