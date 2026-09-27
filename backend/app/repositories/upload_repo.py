"""Upload repository — the sanctioned write surface for ``Upload`` log rows.

Each CSV upload replaces the current inventory outright (no dataset scoping,
no switching). This module just logs that history (filename, timestamp, sku
count) and provides the one destructive action the "delete my upload" button
needs: wiping all inventory + history data back to empty.

Only ``uploads_router.py`` and ``ingest_router.py`` import this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from sqlalchemy import delete as sa_delete, select
from sqlalchemy.orm import Session

from app.models import Case, Recommendation, SalesHistory, Sku, Upload


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string (used for ``uploaded_at``)."""
    return datetime.now(timezone.utc).isoformat()


def create(session: Session, *, filename: str, sku_count: int) -> Upload:
    """Insert a new Upload log row for an accepted CSV upload.

    ``uploaded_at`` is set to the current UTC ISO timestamp.
    """
    row = Upload(filename=filename, uploaded_at=_now_iso(), sku_count=sku_count)
    session.add(row)
    session.flush()
    return row


def list_all(session: Session) -> List[Upload]:
    """Return every Upload row, most recent first (by ``uploaded_at`` descending)."""
    return list(session.scalars(select(Upload).order_by(Upload.uploaded_at.desc())))


def delete_all_data(session: Session) -> None:
    """Wipe all inventory, history, case, recommendation, and upload rows.

    Full reset — there is no per-dataset scoping, so "delete my upload" clears
    everything back to empty. Deletes in dependent-first order:
    ``recommendations``, ``cases``, ``sales_history``, ``skus``, ``uploads``.
    """
    session.execute(sa_delete(Recommendation))
    session.execute(sa_delete(Case))
    session.execute(sa_delete(SalesHistory))
    session.execute(sa_delete(Sku))
    session.execute(sa_delete(Upload))
    session.flush()
