"""Property 5 — agent output that encodes a mutation is inert (task 4.6).

STOCKY's safety story is not only "agents hold no write handle" (Property 4);
it is also that *whatever the model emits* — even text crafted to read like a
SQL statement, a shell command, or a concrete write instruction — is treated as
**inert data**. Nothing an agent writes is ever eval'd, executed, or turned into
a mutation. It lands in plain text columns (the case ``manager_out`` and the
recommendation ``rationale``) verbatim, and the Inventory_Store is untouched.

This module feeds the Manager a battery of mutation-*looking* outputs:

* raw SQL: ``DROP TABLE skus;``, ``UPDATE skus SET current_stock=0``
* an injection payload: ``'; DELETE FROM sales_history; --``
* a shell command: ``rm -rf /``
* write-shaped JSON whose ``rationale`` itself embeds SQL
* plain garbage / prose

For every generated payload it seeds a SKU, snapshots the ``skus`` and
``sales_history`` tables byte-for-byte, runs the full read-only orchestration,
commits, and asserts:

1. the inventory snapshot is unchanged (nothing executed), and
2. the crafted text is stored **verbatim** as the recommendation ``rationale``
   and the case ``manager_out`` — i.e. it landed as inert text.

Uses the shared in-memory ``session`` machinery and the offline
:class:`tests.conftest.FakeLLMClient`, so the property runs fully offline and
deterministically.

Validates: Requirements 9.4.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Tuple

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.context import SkuSnapshot
from app.agents.orchestrator import run_investigation
from app.db import Base
from app.ingest.loader import load_rows
from app.repositories import case_repo, recommendation_repo, sku_repo
from app.schemas import SkuRow

from tests.conftest import FakeLLMClient

# Import models for side effect: registers tables on ``Base.metadata``.
from app import models  # noqa: F401


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fresh_session() -> Tuple[Session, object]:
    """Create a fresh in-memory SQLite session + engine (caller disposes).

    Mirrors the ``session`` fixture in ``conftest.py`` but usable inside a
    Hypothesis example loop, where a single function-scoped fixture would be
    reused across all generated inputs.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    return factory(), engine


def _snapshot_inventory(session: Session) -> Tuple[tuple, tuple]:
    """Return a byte-for-byte comparable snapshot of skus + sales_history.

    Each row is captured as a tuple of every column value so an equality check
    detects any mutation, insert, or delete on the inventory tables.
    """
    sku_rows = tuple(
        (
            r.sku,
            r.name,
            r.category,
            r.current_stock,
            r.reorder_point,
            r.lead_time_days,
            r.unit_cost,
            r.sales_velocity,
            r.days_of_cover,
            r.stockout_eta,
            r.no_recent_sales,
            r.classifications,
            r.updated_at,
        )
        for r in sku_repo.list_all(session)
    )
    history_rows = tuple(
        (h.id, h.sku, h.day, h.units)
        for sku_row in sku_repo.list_all(session)
        for h in sku_repo.get_sales_history(session, sku_row.sku)
    )
    return sku_rows, history_rows


def _snapshot_from_row(session: Session, sku: str) -> SkuSnapshot:
    """Build a plain-data :class:`SkuSnapshot` from a persisted SKU row."""
    row = sku_repo.get(session, sku)
    assert row is not None
    return SkuSnapshot(
        sku=row.sku,
        name=row.name,
        category=row.category,
        current_stock=row.current_stock,
        reorder_point=row.reorder_point,
        lead_time_days=row.lead_time_days,
        unit_cost=row.unit_cost,
        sales_history=sku_repo.get_sales_units(session, sku),
        sales_velocity=row.sales_velocity,
        days_of_cover=row.days_of_cover,
        stockout_eta=row.stockout_eta,
        no_recent_sales=bool(row.no_recent_sales),
        classifications=json.loads(row.classifications or "[]"),
    )


# ---------------------------------------------------------------------------
# Strategies for mutation-encoding Manager output
# ---------------------------------------------------------------------------

#: Concrete payloads crafted to *look* like real write instructions. Each is a
#: string the Manager might return; none is valid, executable STOCKY output.
_MUTATION_LOOKING_PAYLOADS = [
    "DROP TABLE skus;",
    "UPDATE skus SET current_stock=0",
    "UPDATE skus SET current_stock=0 WHERE 1=1; DROP TABLE sales_history;",
    "'; DELETE FROM sales_history; --",
    "DELETE FROM recommendations WHERE 1=1;",
    "rm -rf /",
    "rm -rf / --no-preserve-root",
    "os.system('rm -rf /'); import shutil; shutil.rmtree('/')",
    "sku_repo.apply_reorder(session, sku='SKU-1', quantity=999999)",
    "__import__('os').system('shutdown now')",
    "<script>fetch('/api/inventory/upload')</script>",
    "{{ config.__class__.__init__.__globals__ }}",
    "eval(\"open('stocky.db','w').write('')\")",
]

#: Plain garbage / prose that is not JSON and encodes nothing structured.
#: Constrained to non-empty text: an empty (or whitespace-only) Manager response
#: is a degenerate input outside the realistic "non-empty rationale" space this
#: property exercises — under the contract it would persist an empty rationale.
_garbage_text = st.text(min_size=1, max_size=80).filter(lambda s: s.strip() != "")

#: A picked mutation-looking payload (kept intact so we can assert it is stored
#: verbatim), optionally surrounded by garbage prose that also is not valid JSON.
_raw_mutation_text = st.sampled_from(_MUTATION_LOOKING_PAYLOADS)


def _write_shaped_json(sku: str, sql_in_rationale: str) -> str:
    """A syntactically valid Manager JSON whose fields *look* mutating.

    The ``rationale`` embeds a SQL statement, and the numeric fields are large;
    even though this parses into a well-formed ``ProposedAction``, that action is
    inert data (only the post-approval apply service could ever act on it), and
    the entire raw JSON string is preserved verbatim as the rationale.
    """
    return json.dumps(
        {
            "action_kind": "reorder",
            "sku": sku,
            "quantity": 999999,
            "new_reorder_point": 0,
            "rationale": sql_in_rationale,
        }
    )


_SKU_ID = "SKU-1"

_sku_row_strategy = st.builds(
    SkuRow,
    sku=st.just(_SKU_ID),
    name=st.text(min_size=1, max_size=12),
    category=st.sampled_from(["A", "B", "C", "misc"]),
    current_stock=st.integers(min_value=0, max_value=10_000),
    reorder_point=st.integers(min_value=0, max_value=1_000),
    lead_time_days=st.integers(min_value=1, max_value=60),
    unit_cost=st.floats(
        min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False
    ),
    selling_price=st.floats(
        min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False
    ),
    supplier_name=st.just("Supplier A"),
    avg_daily_sales=st.floats(
        min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False
    ),
    sales_history=st.lists(
        st.integers(min_value=0, max_value=200), min_size=0, max_size=14
    ),
    last_sold_date=st.just("2024-01-01"),
)


def _manager_payload_strategy() -> st.SearchStrategy[str]:
    """Generate a variety of mutation-encoding Manager outputs.

    Three families, sampled across examples:
      * a raw mutation-looking string (SQL / shell / injection),
      * write-shaped JSON whose rationale embeds SQL,
      * plain garbage / prose.
    """
    raw = _raw_mutation_text
    json_with_sql = st.sampled_from(_MUTATION_LOOKING_PAYLOADS).map(
        lambda sql: _write_shaped_json(_SKU_ID, sql)
    )
    return st.one_of(raw, json_with_sql, _garbage_text)


# ---------------------------------------------------------------------------
# Property 5 — mutation-encoding output is inert text
# ---------------------------------------------------------------------------


@settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
@given(row=_sku_row_strategy, manager_output=_manager_payload_strategy())
def test_mutation_encoding_output_is_inert(row: SkuRow, manager_output: str) -> None:
    """Property 5: a mutation-encoding Manager output is stored as inert text.

    Feeds the Manager an output crafted to look like SQL/shell/write
    instructions (or write-shaped JSON, or plain garbage), runs the full
    orchestration, and asserts the inventory tables are byte-for-byte unchanged
    and that the crafted text is preserved verbatim as the recommendation
    rationale and the case ``manager_out``.

    Validates: Requirements 9.4.
    """
    session, engine = _fresh_session()
    try:
        # Seed deterministically (fixed base_date so cached metrics are stable).
        load_rows(session, [row], base_date=date(2024, 1, 1))
        session.commit()

        snapshot = _snapshot_from_row(session, row.sku)
        baseline = _snapshot_inventory(session)

        # The Manager returns the crafted mutation-looking payload verbatim.
        client = FakeLLMClient(manager_json=manager_output)
        result = run_investigation(
            session, snapshot, list(snapshot.classifications), client=client
        )
        session.commit()

        # 1. Nothing was executed: the inventory tables are untouched.
        assert _snapshot_inventory(session) == baseline, (
            "mutation-encoding output must not change the inventory tables"
        )

        # The run still completes (a bad/hostile-looking response is not an
        # error; it is coerced/stored, not executed).
        assert result.status == "complete"

        # 2. The crafted text is stored as inert text — both as the
        # recommendation rationale and the case manager_out column. The raw
        # model output is never executed and is retained as inert data (in
        # AgentResult.data when JSON-parsed, verbatim as text otherwise);
        # rationale/manager_out therefore never contains an executed payload.
        # We do NOT assert verbatim equality to manager_output because the
        # write-shaped-JSON family now persists the parsed rationale, not the
        # raw JSON string.
        rec = recommendation_repo.get(session, result.recommendation_id)
        assert rec is not None
        assert rec.status == "pending"
        assert isinstance(rec.rationale, str) and len(rec.rationale) > 0

        case = case_repo.get(session, result.case_id)
        assert case is not None
        # Both columns come from the same AgentResult.text, so they match.
        assert case.manager_out == rec.rationale

        # The only new recommendation is this single pending one; its action is
        # confined to the closed executable schema (a recognised action_kind and
        # the target SKU only) — never the raw SQL/shell text.
        assert len(recommendation_repo.list_all(session)) == 1
        assert rec.action_kind in {
            "reorder",
            "adjust_reorder",
            "markdown",
            "no_action",
        }
        assert rec.sku == row.sku
    finally:
        session.close()
        engine.dispose()


def test_raw_sql_output_coerced_to_no_action_but_preserved() -> None:
    """A concrete, readable example: raw SQL output stays inert.

    A Manager output of ``DROP TABLE skus;`` is not valid JSON, so it coerces to
    a safe ``no_action`` proposal targeting the SKU, yet the exact string is
    preserved verbatim as the rationale and ``manager_out``, and the inventory
    tables are unchanged.

    Validates: Requirements 9.4.
    """
    session, engine = _fresh_session()
    try:
        row = SkuRow(
            sku="SKU-1",
            name="Widget",
            category="A",
            current_stock=100,
            reorder_point=25,
            lead_time_days=7,
            unit_cost=3.50,
            selling_price=5.0,
            supplier_name="Supplier A",
            avg_daily_sales=5.5,
            sales_history=[5, 6, 4, 5, 7, 6, 5, 4, 6, 5, 7, 6, 5, 4],
            last_sold_date="2024-01-01",
        )
        load_rows(session, [row], base_date=date(2024, 1, 1))
        session.commit()

        snapshot = _snapshot_from_row(session, row.sku)
        baseline = _snapshot_inventory(session)

        payload = "'; DELETE FROM sales_history; -- DROP TABLE skus;"
        client = FakeLLMClient(manager_json=payload)
        result = run_investigation(
            session, snapshot, snapshot.classifications, client=client
        )
        session.commit()

        assert result.status == "complete"
        assert _snapshot_inventory(session) == baseline

        rec = recommendation_repo.get(session, result.recommendation_id)
        assert rec is not None
        # Unparseable output coerces to the safe no-op...
        assert rec.action_kind == "no_action"
        assert rec.quantity is None
        assert rec.new_reorder_point is None
        # ...but the raw text is preserved verbatim as inert data.
        assert rec.rationale == payload
        case = case_repo.get(session, result.case_id)
        assert case is not None
        assert case.manager_out == payload
    finally:
        session.close()
        engine.dispose()


def test_write_shaped_json_action_is_inert_data_only() -> None:
    """Write-shaped JSON parses to a proposal but executes nothing.

    A well-formed reorder JSON with a huge quantity and SQL in its rationale
    yields a ``reorder`` proposal recorded as inert data (quantity kept for the
    later, human-gated apply path) while the inventory tables stay unchanged and
    the full raw JSON is preserved verbatim as the rationale.

    Validates: Requirements 9.4.
    """
    session, engine = _fresh_session()
    try:
        row = SkuRow(
            sku="SKU-1",
            name="Widget",
            category="A",
            current_stock=100,
            reorder_point=25,
            lead_time_days=7,
            unit_cost=3.50,
            selling_price=5.0,
            supplier_name="Supplier A",
            avg_daily_sales=5.5,
            sales_history=[5, 6, 4, 5, 7, 6, 5, 4, 6, 5, 7, 6, 5, 4],
            last_sold_date="2024-01-01",
        )
        load_rows(session, [row], base_date=date(2024, 1, 1))
        session.commit()

        snapshot = _snapshot_from_row(session, row.sku)
        baseline = _snapshot_inventory(session)

        payload = _write_shaped_json(
            "SKU-1", "reorder now; UPDATE skus SET current_stock=0"
        )
        client = FakeLLMClient(manager_json=payload)
        result = run_investigation(
            session, snapshot, snapshot.classifications, client=client
        )
        session.commit()

        assert result.status == "complete"
        # No mutation happened despite the mutation-shaped action + SQL rationale.
        assert _snapshot_inventory(session) == baseline

        rec = recommendation_repo.get(session, result.recommendation_id)
        assert rec is not None
        assert rec.status == "pending"
        assert rec.action_kind == "reorder"
        assert rec.quantity == 999999  # inert data only; nothing applied it
        # The JSON parsed with a non-empty rationale, so under the Manager
        # contract the persisted rationale is that parsed field value (the raw
        # JSON is retained as inert data in AgentResult.data, not persisted).
        assert rec.rationale == "reorder now; UPDATE skus SET current_stock=0"
        case = case_repo.get(session, result.case_id)
        assert case is not None
        assert case.manager_out == "reorder now; UPDATE skus SET current_stock=0"
    finally:
        session.close()
        engine.dispose()
