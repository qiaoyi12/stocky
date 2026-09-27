"""Deterministic ingest loader for STOCKY (Requirements 1.2, 2.1, 3.7).

Persists the validated :class:`~app.schemas.SkuRow` records produced by
:func:`app.ingest.csv_parser.parse_csv` into the Inventory_Store, then runs the
deterministic detection step that caches metrics + classifications on each
``skus`` row. This is the deterministic write half of ingest: it runs exactly
once per upload, contains no business logic beyond persistence + the pure
detection computation, and never touches an LLM.

The module is split into two concerns:

* **Persistence** — :func:`load_rows` upserts the source-of-truth SKU fields and
  replaces the sales-history series, going exclusively through the sanctioned
  ``sku_repo`` inventory-write functions.
* **Detection caching** — :func:`recompute_metrics_for` (single SKU) and
  :func:`recompute_all_metrics` (every SKU) call the pure
  :func:`app.detection.metrics.compute_metrics` (fed the SKU's authoritative
  ``avg_daily_sales``) then :func:`app.detection.classify.classify` (fed the
  stored ``sales_history`` purely for the trend/anomaly rule), and persist the
  result through the sanctioned :func:`sku_repo.set_metrics` writer.
  ``load_rows`` folds this recompute in so ingest ends with metrics populated
  (Req 2.1, 3.7).

The recompute functions are deliberately reusable: Chaos Mode (task 13.x) can
call :func:`recompute_metrics_for` / :func:`recompute_all_metrics` on affected
SKUs after injecting events, so the classification refresh path has a single
deterministic implementation.

Safety boundary
---------------
The loader is one of the two legitimate callers of the inventory-write
functions in :mod:`app.repositories.sku_repo` (the other being the
post-approval Apply-Action Service). It calls only :func:`sku_repo.upsert_sku`,
:func:`sku_repo.replace_sales_history`, and :func:`sku_repo.set_metrics` — the
sanctioned inventory write path — never mutating rows directly. Metric
computation itself is pure (no I/O, no LLM); the loader is the thin
deterministic seam that feeds stored data into those pure functions and writes
the result back. Determinism follows: re-loading the same rows upserts the same
core fields, replaces (rather than appends) the sales history, and recomputes
the same cached metrics, so a repeated ingest is idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence

from sqlalchemy.orm import Session

from app.detection.classify import classify
from app.detection.metrics import compute_metrics
from app.repositories import sku_repo
from app.schemas import SkuRow


@dataclass(frozen=True)
class _ClassifyInput:
    """Plain-data snapshot fed to :func:`app.detection.classify.classify`.

    The persisted ``skus`` row does not expose the ``sales_history`` sequence
    the classifier's ``SkuLike`` protocol requires (history lives in the
    ``sales_history`` table), so the recompute step assembles this lightweight,
    immutable snapshot from the stored SKU fields plus the sales-history units
    read back through the repository. Nothing here is written to the store.
    """

    current_stock: int
    reorder_point: int
    lead_time_days: int
    sales_history: Sequence[int]


def load_rows(
    session: Session,
    rows: Sequence[SkuRow],
    *,
    base_date: Optional[date] = None,
) -> int:
    """Persist parsed CSV rows then cache detection metrics (Req 1.2, 2.1, 3.7).

    For each :class:`~app.schemas.SkuRow` this upserts the core SKU fields and
    replaces its sales-history series, going exclusively through the sanctioned
    ``sku_repo`` inventory-write functions. After all rows are persisted it runs
    the deterministic detection step for each loaded SKU
    (:func:`recompute_metrics_for`), caching ``sales_velocity``,
    ``days_of_cover``, ``stockout_eta``, ``no_recent_sales`` and
    ``classifications`` on the row.

    The write is deterministic and meant to run once at upload time; re-running
    it with the same rows is idempotent (fields are upserted, history replaced,
    and metrics recomputed to the same values).

    Args:
        session: An open SQLAlchemy session. The caller owns the transaction
            boundary (commit/rollback); this function only flushes via the
            repository writers so the loaded state is queryable within the same
            session before commit.
        rows: The accepted :class:`~app.schemas.SkuRow` records from
            :func:`app.ingest.csv_parser.parse_csv`.
        base_date: Optional anchor date. It is forwarded to
            :func:`sku_repo.replace_sales_history` for the sales-history day
            assignment and used as ``today`` when projecting ``stockout_eta``,
            so both the persisted history dates and the cached ETA are
            deterministic (useful for tests). Defaults to today when omitted.

    Returns:
        The number of SKU rows persisted.
    """
    count = 0
    for row in rows:
        sku_repo.upsert_sku(
            session,
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
        )
        sku_repo.replace_sales_history(
            session,
            sku=row.sku,
            units=row.sales_history,
            base_date=base_date,
        )
        count += 1

    # Deterministic detection step: now that every row (and its sales history)
    # is persisted, compute and cache metrics + classifications (Req 2.1, 3.7).
    for row in rows:
        recompute_metrics_for(session, row.sku, today=base_date)

    return count


def recompute_metrics_for(
    session: Session,
    sku: str,
    *,
    today: Optional[date] = None,
) -> None:
    """Recompute and cache detection metrics + classifications for one SKU.

    Reads the persisted SKU row and its sales-history units through the
    ``sku_repo`` read functions, runs the pure Detection_Engine functions
    (:func:`app.detection.metrics.compute_metrics` then
    :func:`app.detection.classify.classify`), and writes the result back via the
    sanctioned :func:`sku_repo.set_metrics` writer. Fully deterministic and free
    of any LLM call (Req 2.1, 2.4, 3.7).

    This is the single reusable recompute entry point: the deterministic ingest
    path calls it per loaded SKU, and Chaos Mode (task 13.x) can call it on the
    SKUs it perturbs so classification refresh has one implementation.

    Args:
        session: An open SQLAlchemy session (caller owns the transaction).
        sku: The SKU id to recompute. Must already exist in the store.
        today: Base date for the ``stockout_eta`` projection, forwarded to
            :func:`~app.detection.metrics.compute_metrics`. Defaults to the
            current date when omitted; supply it for deterministic ETAs.

    Raises:
        KeyError: If ``sku`` does not exist in the store (surfaced by
            :func:`sku_repo.set_metrics`).
    """
    row = sku_repo.get(session, sku)
    if row is None:
        raise KeyError(f"unknown sku: {sku!r}")

    units: List[int] = sku_repo.get_sales_units(session, sku)

    # avg_daily_sales is the authoritative velocity source; sales_history is
    # still read here (via get_sales_units) because classify()'s trend rule
    # needs it, but it no longer feeds compute_metrics.
    metrics = compute_metrics(row.current_stock, row.avg_daily_sales, today=today)

    snapshot = _ClassifyInput(
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        sales_history=units,
    )
    classifications = classify(
        snapshot,
        sales_velocity=metrics.sales_velocity,
        days_of_cover=metrics.days_of_cover,
    )

    sku_repo.set_metrics(
        session,
        sku=sku,
        sales_velocity=metrics.sales_velocity,
        days_of_cover=metrics.days_of_cover,
        stockout_eta=metrics.stockout_eta,
        no_recent_sales=metrics.no_recent_sales,
        classifications=classifications,
    )


def recompute_all_metrics(
    session: Session,
    *,
    today: Optional[date] = None,
) -> int:
    """Recompute cached metrics + classifications for every stored SKU.

    Convenience wrapper over :func:`recompute_metrics_for` that iterates every
    SKU (via :func:`sku_repo.list_all`) in deterministic id order. Useful for a
    full refresh after a bulk change and reusable by Chaos Mode when many SKUs
    are affected.

    Args:
        session: An open SQLAlchemy session (caller owns the transaction).
        today: Base date for ``stockout_eta`` projection, forwarded to each
            :func:`recompute_metrics_for` call.

    Returns:
        The number of SKUs recomputed.
    """
    count = 0
    for row in sku_repo.list_all(session):
        recompute_metrics_for(session, row.sku, today=today)
        count += 1
    return count
