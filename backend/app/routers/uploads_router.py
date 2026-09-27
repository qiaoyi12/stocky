"""Uploads API router — history log + full data reset.

Exposes:

* ``GET /api/uploads`` — the upload history (filename, timestamp, sku count),
  most recent first.
* ``DELETE /api/uploads`` — deletes all uploaded inventory data (no id: there
  is no dataset scoping, so this wipes everything back to empty). Backs the
  "delete my upload" button.

This router is a thin HTTP seam only; all persistence lives in
``app.repositories.upload_repo``.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.repositories import upload_repo
from app.schemas import UploadSummary

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.get("", response_model=List[UploadSummary])
def list_uploads(session: Session = Depends(get_session)) -> List[UploadSummary]:
    """Return every logged upload, most recent first."""
    rows = upload_repo.list_all(session)
    return [
        UploadSummary(
            id=row.id,
            filename=row.filename,
            uploaded_at=row.uploaded_at,
            sku_count=row.sku_count,
        )
        for row in rows
    ]


@router.delete("", status_code=204)
def delete_all_uploads(session: Session = Depends(get_session)) -> Response:
    """Delete all uploaded inventory data, resetting the app to empty."""
    try:
        upload_repo.delete_all_data(session)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return Response(status_code=204)
