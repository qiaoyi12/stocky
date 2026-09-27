"""Dataset repository — the sanctioned write surface for ``Dataset`` rows.

This module joins ``sku_repo.py``'s write section and ``apply_action.py`` as
one of STOCKY's sanctioned write surfaces (design "Safety Boundary"). Like
``sku_repo.py`` it is split into two clearly separated sections:

* ``READ FUNCTIONS`` — pure lookups used freely across the app, including by
  every scoped read path to resolve the active dataset id.
* ``WRITE FUNCTIONS`` — the ONLY functions in the whole system that create,
  activate, rename, or delete ``Dataset`` rows (and perform the cascading
  delete of their dependent ``skus``/``sales_history``/``cases``/
  ``recommendations`` rows).

No agent module (``app/agents/*.py``) and no orchestrator module imports the
write functions — that isolation is what the safety-boundary import-graph
test asserts (design "The Safety Boundary", Req 10.3). Only
``datasets_router.py`` and ``ingest_router.py`` import this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.orm import Session

from app.models import Case, Dataset, Recommendation, SalesHistory, Sku
from app.schemas import DatasetSummary


def _now_iso() -> str:
    """Current UTC timestamp as an ISO-8601 string (used for ``uploaded_at``)."""
    return datetime.now(timezone.utc).isoformat()


class UnknownDataset(Exception):
    """Raised when a ``dataset_id`` does not resolve to an existing Dataset row."""


class EmptyDisplayName(Exception):
    """Raised when a rename submits a Display_Name that is empty or whitespace-only."""


# ===========================================================================
# READ FUNCTIONS
# ---------------------------------------------------------------------------
# Pure lookups. Safe to import anywhere, including read-only services and
# every scoped read path that needs to resolve the active dataset id. These
# never mutate dataset state.
# ===========================================================================


def get(session: Session, dataset_id: int) -> Optional[Dataset]:
    """Return the Dataset row for ``dataset_id``, or ``None`` if it does not exist."""
    return session.get(Dataset, dataset_id)


def get_active(session: Session) -> Optional[Dataset]:
    """Return the currently active Dataset row, or ``None`` if none is active."""
    return session.scalars(
        select(Dataset).where(Dataset.is_active == 1)
    ).first()


def get_active_id(session: Session) -> Optional[int]:
    """Return the id of the currently active Dataset, or ``None`` if none is active.

    Convenience used by every scoped read path (inventory, dashboard,
    warehouse, impact, agents/cases) to resolve the dataset to filter on;
    callers treat ``None`` as "no active dataset -> empty result" (Req 2.4).
    """
    active = get_active(session)
    return active.id if active is not None else None


def list_with_counts(session: Session) -> List[DatasetSummary]:
    """Return one ``DatasetSummary`` per Dataset row, with an accurate SKU count.

    ``sku_count`` is computed via a ``COUNT(*)`` query on ``skus`` filtered by
    each dataset's id (Req 5.1, 5.2).
    """
    datasets = list(session.scalars(select(Dataset).order_by(Dataset.id)))
    summaries: List[DatasetSummary] = []
    for dataset in datasets:
        sku_count = session.scalar(
            select(func.count()).select_from(Sku).where(Sku.dataset_id == dataset.id)
        )
        summaries.append(
            DatasetSummary(
                id=dataset.id,
                filename=dataset.filename,
                display_name=dataset.display_name,
                uploaded_at=dataset.uploaded_at,
                sku_count=sku_count or 0,
                is_active=bool(dataset.is_active),
            )
        )
    return summaries


# ===========================================================================
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  WRITE FUNCTIONS — THE ONLY DATASET MUTATORS                              │
# ├─────────────────────────────────────────────────────────────────────────┤
# │  Everything below this banner creates, activates, renames, or deletes     │
# │  ``Dataset`` rows (and performs the cascading delete of their dependent    │
# │  ``skus``/``sales_history``/``cases``/``recommendations`` rows).           │
# │  ONLY ``datasets_router.py`` and ``ingest_router.py`` may import these     │
# │  functions. Agent and orchestrator modules MUST NOT import anything        │
# │  below this line (enforced by the safety-boundary import-graph test —      │
# │  Req 10.3).                                                                 │
# └─────────────────────────────────────────────────────────────────────────┘
# ===========================================================================


def create(session: Session, *, filename: str) -> Dataset:
    """Insert a new Dataset row for an uploaded CSV (Req 3.1, 3.4).

    ``display_name`` is initialized to ``filename`` and ``is_active`` starts at
    ``0`` — activation is a separate, explicit step performed by the caller
    once loading succeeds (see :func:`activate`).
    """
    row = Dataset(
        filename=filename,
        display_name=filename,
        uploaded_at=_now_iso(),
        is_active=0,
    )
    session.add(row)
    session.flush()
    return row


def activate(session: Session, dataset_id: int) -> Dataset:
    """Make ``dataset_id`` the sole active Dataset (Req 2.1, 2.2, 6.1).

    Raises ``UnknownDataset`` if the id does not resolve. Otherwise clears
    ``is_active`` on every Dataset row, then sets ``is_active = 1`` on the
    target row. Touches only ``datasets.is_active`` — never
    ``skus``/``cases``/``recommendations`` (Req 2.3).
    """
    row = session.get(Dataset, dataset_id)
    if row is None:
        raise UnknownDataset(f"unknown dataset: {dataset_id!r}")

    for other in session.scalars(select(Dataset)):
        other.is_active = 0
    row.is_active = 1
    session.flush()
    return row


def rename(session: Session, dataset_id: int, *, display_name: str) -> Dataset:
    """Update a Dataset's Display_Name (Req 7.1, 7.3).

    Raises ``UnknownDataset`` if the id does not resolve; raises
    ``EmptyDisplayName`` if ``display_name.strip()`` is empty. Otherwise sets
    ``display_name`` and flushes.
    """
    row = session.get(Dataset, dataset_id)
    if row is None:
        raise UnknownDataset(f"unknown dataset: {dataset_id!r}")
    if not display_name.strip():
        raise EmptyDisplayName("display_name must not be empty or whitespace-only")
    row.display_name = display_name
    session.flush()
    return row


def delete(session: Session, dataset_id: int) -> None:
    """Delete a Dataset and cascade-delete its dependent rows (Req 4.1-4.6).

    Raises ``UnknownDataset`` if the id does not resolve — checked before any
    delete statement runs, so a not-found request makes zero changes.
    Otherwise deletes, in dependent-first order: ``recommendations``,
    ``cases``, ``sales_history``, ``skus``, then the ``Dataset`` row itself.
    Does not touch ``is_active`` on any other dataset row.
    """
    row = session.get(Dataset, dataset_id)
    if row is None:
        raise UnknownDataset(f"unknown dataset: {dataset_id!r}")

    session.execute(sa_delete(Recommendation).where(Recommendation.dataset_id == dataset_id))
    session.execute(sa_delete(Case).where(Case.dataset_id == dataset_id))
    session.execute(sa_delete(SalesHistory).where(SalesHistory.dataset_id == dataset_id))
    session.execute(sa_delete(Sku).where(Sku.dataset_id == dataset_id))
    session.delete(row)
    session.flush()
