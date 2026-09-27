"""Safety-boundary tests — the automated evidence for STOCKY's core guarantee.

Two complementary kinds of check, matching task 4.5 and design Property 4:

1. **Import-graph assertion.** The ``app.agents.orchestrator`` module graph must
   never import the deterministic inventory writer (``app.services.apply_action``)
   nor any of the ``sku_repo`` inventory-*write* functions. Enforced two ways:
   a runtime walk of the transitive import closure via ``sys.modules``, and a
   static AST parse of every source file in the ``app/agents`` package so the
   guarantee holds regardless of runtime import order.

2. **No-mutation property (Property 4).** For any store state and any flagged
   SKU, running any single agent (Detective/Forecast/Strategy/Manager) or the
   full orchestration leaves every ``skus`` and ``sales_history`` row
   byte-for-byte unchanged; the only new row an investigation may create is a
   ``recommendations`` row with status ``pending``. Verified with Hypothesis
   over generated snapshots, using a fake offline LLM client.

Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import agents
from app.agents import detective, forecast, manager, strategy
from app.agents.context import AgentContext, SkuSnapshot
from app.agents.orchestrator import run_investigation
from app.db import Base
from app.ingest.loader import load_rows
from app.repositories import recommendation_repo, sku_repo
from app.schemas import SkuRow

from tests.conftest import FakeLLMClient

# Import models for side effect: registers tables on ``Base.metadata``.
from app import models  # noqa: F401


# ---------------------------------------------------------------------------
# Forbidden write surface
# ---------------------------------------------------------------------------

#: The deterministic inventory writer module. The agent/orchestrator graph must
#: never import it (design "The Safety Boundary": "Only one writer").
FORBIDDEN_MODULE = "app.services.apply_action"

#: The inventory-*write* function names in ``sku_repo``. Read functions are fine;
#: these mutate ``skus`` / ``sales_history`` and must never be imported by an
#: agent or the orchestrator.
FORBIDDEN_SKU_REPO_WRITES: Set[str] = {
    "upsert_sku",
    "replace_sales_history",
    "set_metrics",
    "apply_reorder",
    "apply_adjust_reorder",
}


# ---------------------------------------------------------------------------
# 1. Import-graph assertions
# ---------------------------------------------------------------------------


def _transitive_app_imports(root_module_name: str) -> Set[str]:
    """Return the transitive closure of ``app.*`` modules reachable from a root.

    Imports the root module, then walks ``sys.modules`` following each module's
    imported submodule references. We restrict the walk to modules under the
    ``app`` package (the surface we care about) and follow attributes that are
    themselves modules to approximate the runtime import graph.
    """
    importlib.import_module(root_module_name)

    seen: Set[str] = set()
    stack: List[str] = [root_module_name]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        module = sys.modules.get(name)
        if module is None:
            continue
        # Follow module-valued attributes and any already-imported submodules.
        for attr in vars(module).values():
            sub = getattr(attr, "__name__", None)
            if isinstance(sub, str) and sub.startswith("app") and sub in sys.modules:
                stack.append(sub)
        # Also follow declared child modules present in sys.modules.
        prefix = name + "."
        for mod_name in list(sys.modules):
            if mod_name.startswith(prefix):
                stack.append(mod_name)

    return {m for m in seen if m.startswith("app")}


def test_orchestrator_import_closure_excludes_apply_action() -> None:
    """The orchestrator's runtime import closure never pulls in the writer.

    Validates: Requirements 9.1, 9.2 (no write capability reaches the agents).
    """
    closure = _transitive_app_imports("app.agents.orchestrator")
    assert FORBIDDEN_MODULE not in closure, (
        f"orchestrator import closure must not include {FORBIDDEN_MODULE}; "
        f"found modules: {sorted(closure)}"
    )
    # No submodule of the services.apply_action path should appear either.
    offenders = {m for m in closure if m == FORBIDDEN_MODULE or m.startswith(FORBIDDEN_MODULE + ".")}
    assert not offenders, f"forbidden writer modules in closure: {sorted(offenders)}"


def _agents_package_source_files() -> List[Path]:
    """Return every ``.py`` source file in the ``app.agents`` package."""
    pkg_dir = Path(agents.__file__).parent
    return sorted(p for p in pkg_dir.glob("*.py"))


def _imported_names_from_source(path: Path) -> Tuple[Set[str], Dict[str, Set[str]]]:
    """Statically extract imports from a source file.

    Returns a tuple of:
      - the set of fully-qualified module names imported (``import x`` / the
        module side of ``from x import y``), and
      - a mapping of module -> set of names imported via ``from module import name``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: Set[str] = set()
    from_imports: Dict[str, Set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            modules.add(node.module)
            names = {alias.name for alias in node.names}
            from_imports.setdefault(node.module, set()).update(names)
    return modules, from_imports


def test_agents_package_never_imports_apply_action_statically() -> None:
    """No source file in ``app/agents`` imports the deterministic writer.

    A static AST check that holds independently of runtime import order.

    Validates: Requirements 9.1, 9.2.
    """
    offenders: List[str] = []
    for path in _agents_package_source_files():
        modules, _ = _imported_names_from_source(path)
        for mod in modules:
            if mod == FORBIDDEN_MODULE or mod.startswith(FORBIDDEN_MODULE + "."):
                offenders.append(f"{path.name} imports {mod}")
    assert not offenders, "agents package imports the writer: " + "; ".join(offenders)


def test_agents_package_never_imports_sku_repo_write_functions() -> None:
    """No source file in ``app/agents`` imports a ``sku_repo`` write function.

    Covers both ``from app.repositories.sku_repo import <writer>`` and importing
    the module and would-be use of its writers. Since the orchestrator/agents
    have no legitimate need for ``sku_repo`` at all, we assert the write symbols
    never appear as imported names, and flag any import of the ``sku_repo``
    module by an agents-package file.

    Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2.
    """
    sku_repo_mod = "app.repositories.sku_repo"
    offenders: List[str] = []
    for path in _agents_package_source_files():
        modules, from_imports = _imported_names_from_source(path)

        # Direct ``from app.repositories.sku_repo import <name>``.
        imported_write_names = from_imports.get(sku_repo_mod, set()) & FORBIDDEN_SKU_REPO_WRITES
        for name in sorted(imported_write_names):
            offenders.append(f"{path.name} imports write function {sku_repo_mod}.{name}")

        # Importing the sku_repo module wholesale from an agents file is also a
        # boundary smell — the read-only agent layer has no need for it.
        if sku_repo_mod in modules:
            offenders.append(f"{path.name} imports module {sku_repo_mod}")

    assert not offenders, "agents package references sku_repo writers: " + "; ".join(offenders)


def test_orchestrator_module_globals_expose_no_writer() -> None:
    """The orchestrator's own module namespace binds no writer symbol.

    A belt-and-braces runtime check: neither the forbidden writer module nor any
    ``sku_repo`` write function is reachable as a top-level name in the
    orchestrator module.
    """
    import app.agents.orchestrator as orch

    for forbidden in FORBIDDEN_SKU_REPO_WRITES:
        bound = getattr(orch, forbidden, None)
        assert bound is None, f"orchestrator unexpectedly binds writer {forbidden!r}"

    # sku_repo itself should not be a name in the orchestrator module.
    assert getattr(orch, "sku_repo", None) is None, "orchestrator binds sku_repo"


# ---------------------------------------------------------------------------
# 2. No-mutation property (Property 4)
# ---------------------------------------------------------------------------


def _fresh_session() -> Tuple[Session, object]:
    """Create a fresh in-memory SQLite session + engine (caller disposes)."""
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

    Each row is captured as a tuple of every column value, ordered
    deterministically, so equality comparison detects any mutation, insert, or
    delete on the inventory tables.
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
    import json

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


# --- Hypothesis strategy for a valid SKU snapshot ---------------------------

_sku_row_strategy = st.builds(
    SkuRow,
    sku=st.just("SKU-1"),
    name=st.text(min_size=1, max_size=12),
    category=st.sampled_from(["A", "B", "C", "misc"]),
    current_stock=st.integers(min_value=0, max_value=10_000),
    reorder_point=st.integers(min_value=0, max_value=1_000),
    lead_time_days=st.integers(min_value=1, max_value=60),
    unit_cost=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    selling_price=st.floats(min_value=0.01, max_value=1000.0, allow_nan=False, allow_infinity=False),
    supplier_name=st.just("Supplier A"),
    avg_daily_sales=st.floats(min_value=0.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    sales_history=st.lists(st.integers(min_value=0, max_value=200), min_size=0, max_size=14),
    last_sold_date=st.just("2024-01-01"),
)


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(row=_sku_row_strategy, manager_kind=st.sampled_from(["reorder", "adjust_reorder", "markdown", "no_action", "bogus"]))
def test_agents_never_mutate_inventory(row: SkuRow, manager_kind: str) -> None:
    """Property 4: no agent nor the full orchestration mutates inventory.

    Seeds a fresh store, snapshots ``skus``/``sales_history``, runs each agent
    individually and then the full orchestration with an offline fake LLM
    client (including a Manager output crafted to *look* like a real mutating
    action), and asserts the inventory tables are byte-for-byte unchanged. The
    only new row permitted is a single ``pending`` recommendation.

    Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2.
    """
    session, engine = _fresh_session()
    try:
        # Seed deterministically (fixed base_date so cached metrics are stable).
        from datetime import date

        load_rows(session, [row], base_date=date(2024, 1, 1))
        session.commit()

        snapshot = _snapshot_from_row(session, row.sku)
        baseline = _snapshot_inventory(session)

        # A Manager response that *encodes* a concrete mutation, to prove even a
        # write-shaped recommendation stays inert (also touches Req 9.4 spirit).
        manager_json = (
            '{"action_kind": "%s", "sku": "%s", "quantity": 500, '
            '"new_reorder_point": 999, "rationale": "DROP TABLE skus; reorder now"}'
            % (manager_kind, row.sku)
        )

        # 1. Run each agent individually against the snapshot.
        ctx = AgentContext(sku=snapshot, classifications=list(snapshot.classifications), prior={})
        for agent in (detective, forecast, strategy):
            client = FakeLLMClient()
            result = agent.run(ctx, client=client)
            ctx = ctx.with_output(agent.NAME, result)
            assert len(client.calls) == 1  # exactly one LLM call per agent
        manager_client = FakeLLMClient(manager_json=manager_json)
        manager.run(ctx, client=manager_client)
        assert len(manager_client.calls) == 1

        # Running the agents changed nothing in the inventory tables.
        assert _snapshot_inventory(session) == baseline

        recs_before = len(recommendation_repo.list_all(session))

        # 2. Run the full orchestration.
        orch_client = FakeLLMClient(manager_json=manager_json)
        result = run_investigation(
            session, snapshot, list(snapshot.classifications), client=orch_client
        )
        session.commit()

        # Inventory tables are byte-for-byte unchanged by the whole run.
        assert _snapshot_inventory(session) == baseline, (
            "orchestration mutated the inventory tables"
        )

        # The only new row is a single pending recommendation.
        recs_after = recommendation_repo.list_all(session)
        assert len(recs_after) == recs_before + 1
        assert result.status == "complete"
        new_rec = recommendation_repo.get(session, result.recommendation_id)
        assert new_rec is not None
        assert new_rec.status == "pending"
    finally:
        session.close()
        engine.dispose()


def test_full_orchestration_leaves_inventory_unchanged_simple() -> None:
    """A plain (non-property) end-to-end check of Property 4 for one SKU.

    Complements the Hypothesis test with a concrete, readable example.

    Validates: Requirements 4.3, 5.3, 6.3, 7.4, 9.1, 9.2.
    """
    from datetime import date

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

        client = FakeLLMClient(
            manager_json='{"action_kind": "reorder", "sku": "SKU-1", '
            '"quantity": 200, "rationale": "restock"}'
        )
        result = run_investigation(session, snapshot, snapshot.classifications, client=client)
        session.commit()

        assert result.status == "complete"
        assert _snapshot_inventory(session) == baseline

        recs = recommendation_repo.list_all(session)
        assert len(recs) == 1
        assert recs[0].status == "pending"
        # The recommendation records the proposed action as inert data only.
        assert recs[0].action_kind == "reorder"
        assert recs[0].quantity == 200
    finally:
        session.close()
        engine.dispose()
