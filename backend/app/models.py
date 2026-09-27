"""SQLAlchemy ORM models for the STOCKY inventory tables.

Five tables, mapped to the design's "Data Models" section:

* ``datasets``        — one row per uploaded CSV; exactly one is active.
* ``skus``            — inventory items plus cached deterministic metrics,
                         scoped to a dataset via a composite ``(dataset_id, sku)`` PK.
* ``sales_history``   — recent daily sales, one row per (dataset_id, sku, day).
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
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.db import Base


class Dataset(Base):
    """A named, timestamped collection of SKU/case/recommendation rows created
    by one CSV upload. Exactly one Dataset is active at a time."""

    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    filename = Column(String, nullable=False)
    display_name = Column(String, nullable=False)
    uploaded_at = Column(String, nullable=False)  # ISO-8601, set at create()
    is_active = Column(Integer, nullable=False, default=0)  # 0/1 flag; SQLite has no native bool


class Sku(Base):
    """An inventory item and its cached, recomputed detection metrics.

    Scoped to a Dataset via a composite ``(dataset_id, sku)`` primary key so
    two datasets may contain rows with the same ``sku`` code without conflict.
    """

    __tablename__ = "skus"

    dataset_id = Column(Integer, ForeignKey("datasets.id"), primary_key=True)
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
    """Recent daily sales for a SKU; one row per (dataset_id, sku, day)."""

    __tablename__ = "sales_history"
    __table_args__ = (
        UniqueConstraint("dataset_id", "sku", "day", name="uq_sales_history_dataset_sku_day"),
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    sku = Column(String, nullable=False)
    day = Column(String, nullable=False)  # ISO date
    units = Column(Integer, nullable=False)


class Case(Base):
    """One agent investigation run and its recorded four-step outputs."""

    __tablename__ = "cases"
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    sku = Column(String, nullable=False)
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
    __table_args__ = (
        ForeignKeyConstraint(["dataset_id", "sku"], ["skus.dataset_id", "skus.sku"]),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(Integer, nullable=False)
    case_id = Column(Integer, ForeignKey("cases.id"), nullable=False)
    sku = Column(String, nullable=False)
    action_kind = Column(String, nullable=False)  # reorder|adjust_reorder|markdown|no_action
    quantity = Column(Integer, nullable=True)  # for reorder
    new_reorder_point = Column(Integer, nullable=True)  # for adjust_reorder
    rationale = Column(Text, nullable=False)  # Manager's supporting text (text only)
    status = Column(String, nullable=False, default="pending")  # pending|approved|rejected|applied
    created_at = Column(String, nullable=False)
    decided_at = Column(String, nullable=True)  # approve/reject time
    applied_at = Column(String, nullable=True)  # set by apply-action service
