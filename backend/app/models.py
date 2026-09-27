"""SQLAlchemy ORM models for the STOCKY inventory tables.

Five tables:

* ``uploads``         — a log of past CSV uploads (filename, timestamp, sku
                         count); purely informational, not a live-switchable
                         dataset.
* ``skus``            — inventory items plus cached deterministic metrics,
                         keyed by ``sku``.
* ``sales_history``   — recent daily sales, one row per (sku, day).
* ``cases``           — one agent investigation run and its four outputs.
* ``recommendations`` — a Manager proposal with its lifecycle status.

Only deterministic backend code writes these tables (ingest at upload, and the
apply-action service after approval). Agent-produced text lands in plain text
columns (``*_out``, ``rationale``) as inert data — never executed.
"""

from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.db import Base


class Upload(Base):
    """A log entry for one CSV upload (filename, timestamp, sku count).

    Purely a history log — uploading a new CSV replaces the current inventory
    data outright, so there is nothing to switch between and nothing to
    activate/rename.
    """

    __tablename__ = "uploads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    uploaded_at = Column(String, nullable=False)  # ISO-8601, set at create()
    sku_count = Column(Integer, nullable=False)


class Sku(Base):
    """An inventory item and its cached, recomputed detection metrics."""

    __tablename__ = "skus"

    sku = Column(String, primary_key=True)  # stock-keeping unit id
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    current_stock = Column(Integer, nullable=False)
    reorder_point = Column(Integer, nullable=False)
    lead_time_days = Column(Integer, nullable=False)
    unit_cost = Column(Float, nullable=False)
    selling_price = Column(Float, nullable=False)
    supplier_name = Column(String, nullable=False)
    avg_daily_sales = Column(Float, nullable=False)  # authoritative Sales_Velocity source
    last_sold_date = Column(String, nullable=True)  # nullable for pre-migration rows

    # Cached deterministic metrics (recomputed on ingest / chaos re-run).
    sales_velocity = Column(Float, nullable=True)  # units/day
    days_of_cover = Column(Float, nullable=True)  # NULL when velocity == 0 (Req 2.5)
    stockout_eta = Column(String, nullable=True)  # ISO date, NULL when velocity == 0
    no_recent_sales = Column(Integer, nullable=False, default=0)  # 1 when velocity == 0
    classifications = Column(Text, nullable=False, default="[]")  # JSON array of labels
    updated_at = Column(String, nullable=False)


class SalesHistory(Base):
    """Recent daily sales for a SKU; one row per (sku, day)."""

    __tablename__ = "sales_history"
    __table_args__ = (
        UniqueConstraint("sku", "day", name="uq_sales_history_sku_day"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String, ForeignKey("skus.sku"), nullable=False)
    day = Column(String, nullable=False)  # ISO date
    units = Column(Integer, nullable=False)


class Case(Base):
    """One agent investigation run and its recorded four-step outputs."""

    __tablename__ = "cases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String, ForeignKey("skus.sku"), nullable=False)
    status = Column(String, nullable=False)  # running | complete | error
    failed_step = Column(String, nullable=True)  # detective|forecast|strategy|manager
    detective_out = Column(Text, nullable=True)  # agent output text
    forecast_out = Column(Text, nullable=True)
    strategy_out = Column(Text, nullable=True)
    manager_out = Column(Text, nullable=True)  # raw manager text/rationale
    created_at = Column(String, nullable=False)
    completed_at = Column(String, nullable=True)


class Recommendation(Base):
    """A Manager proposal carrying its lifecycle status (Req 10-12)."""

    __tablename__ = "recommendations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    sku = Column(String, ForeignKey("skus.sku"), nullable=False)
    action_kind = Column(String, nullable=False)  # reorder|adjust_reorder|markdown|no_action
    quantity = Column(Integer, nullable=True)  # for reorder
    new_reorder_point = Column(Integer, nullable=True)  # for adjust_reorder
    rationale = Column(Text, nullable=False)  # Manager's supporting text (text only)
    status = Column(String, nullable=False, default="pending")  # pending|approved|rejected|applied
    created_at = Column(String, nullable=False)
    decided_at = Column(String, nullable=True)  # approve/reject time
    applied_at = Column(String, nullable=True)  # set by apply-action service
