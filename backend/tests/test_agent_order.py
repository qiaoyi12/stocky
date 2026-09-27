"""Property 6 — Fixed sequential order with fail-stop.

Exercises :func:`app.agents.orchestrator.run_investigation` against a mocked
LLM client so no real network call is ever made. The mock records the order in
which agents call it and can be configured to raise :class:`LLMError` on the
Nth call, letting us drive a failure at any step.

The property under test (design.md → Correctness Properties → Property 6):

    For any successful investigation, the recorded execution order equals
    exactly ``[Detective, Forecast, Strategy, Manager]``; and for any run where
    the agent at position ``i`` fails, no agent after position ``i`` executes
    and the reported failed step equals position ``i``.

**Validates: Requirements 8.1, 8.2, 8.3, 4.4, 5.4, 6.4, 7.5**
"""

from __future__ import annotations

import os
import tempfile

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.context import SkuSnapshot
from app.agents.llm_client import LLMError
from app.agents.orchestrator import STEPS, run_investigation

# The fixed order the orchestrator must run its agents in (design Property 6).
EXPECTED_ORDER = ["detective", "forecast", "strategy", "manager"]

# A valid strict-JSON Manager reply so the success path persists a real
# recommendation instead of falling back to NO_ACTION on a parse error.
MANAGER_JSON = '{"action_kind": "no_action", "sku": "X", "rationale": "ok"}'


# --- Mock LLM client --------------------------------------------------------


class RecordingLLMClient:
    """A drop-in stand-in for ``LLMClient`` that never touches the network.

    Every :meth:`call` appends to ``calls`` (recording invocation order) and
    returns canned text. When ``fail_on_call`` is set to a 1-based call index,
    the matching call raises :class:`LLMError` instead of returning — simulating
    an LLM failure at that step so the orchestrator must fail-stop.
    """

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on_call = fail_on_call

    def call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        n = len(self.calls)  # 1-based index of this call
        if self.fail_on_call is not None and n == self.fail_on_call:
            raise LLMError(f"simulated failure on call {n}")
        # The Manager (4th step) needs strict JSON; earlier prose steps ignore it.
        return MANAGER_JSON


# --- Temp-DB session fixture (self-contained; does not touch conftest) ------


@pytest.fixture()
def session():
    """Yield a SQLAlchemy session bound to an isolated temp-file SQLite DB.

    Self-contained so this test file needs no shared ``conftest.py`` fixture: it
    builds a fresh engine on a throwaway file, creates the full schema via the
    app's declarative ``Base`` metadata, and tears the file down afterwards.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )
    # Import for the side effect of registering every model on Base.metadata.
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = Session()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


def _snapshot() -> SkuSnapshot:
    """A minimal, valid SKU snapshot for an investigation run."""
    return SkuSnapshot(
        sku="X",
        name="Widget",
        category="general",
        current_stock=10,
        reorder_point=5,
        lead_time_days=7,
        unit_cost=1.5,
        sales_history=[1, 2, 3],
        sales_velocity=2.0,
        days_of_cover=5.0,
        stockout_eta=None,
        no_recent_sales=False,
        classifications=["low_stock"],
    )


# --- Guard: the orchestrator's fixed order matches what we assert -----------


def test_steps_are_in_the_fixed_expected_order():
    """The orchestrator's STEPS list is exactly Detective→Forecast→Strategy→Manager."""
    assert [step.NAME for step in STEPS] == EXPECTED_ORDER


# --- Success case: all four agents run, in order ----------------------------


def test_success_runs_all_four_agents_in_fixed_order(session):
    """A run with no failures calls the four agents in the fixed order (Req 8.1, 8.2)."""
    client = RecordingLLMClient()

    result = run_investigation(session, _snapshot(), ["low_stock"], client=client)

    assert result.status == "complete"
    assert result.failed_step is None
    assert result.recommendation_id is not None
    # Exactly four LLM calls, one per agent, in the fixed sequential order.
    assert len(client.calls) == len(EXPECTED_ORDER)


# --- Property 6: fail-stop at any position i --------------------------------


@settings(deadline=None)
@given(i=st.integers(min_value=0, max_value=3))
def test_failure_at_position_i_fail_stops(i):
    """A failure at position ``i`` runs no later agent and reports step ``i``.

    Hypothesis picks the failing step index ``i`` in ``0..3``. The mock is
    configured to raise ``LLMError`` on call ``i + 1`` (calls are 1-based). We
    then assert exactly ``i + 1`` calls happened (steps ``0..i`` ran, and none
    after), the case status is ``error``, and ``failed_step`` equals the agent
    NAME at index ``i`` (Req 8.3, 4.4, 5.4, 6.4, 7.5).

    **Validates: Requirements 8.1, 8.2, 8.3, 4.4, 5.4, 6.4, 7.5**
    """
    # Each Hypothesis example needs its own isolated DB (the `session` fixture is
    # function-scoped and cannot be combined with @given), so build one inline.
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )
    from app import models  # noqa: F401
    from app.db import Base

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = Session()
    try:
        client = RecordingLLMClient(fail_on_call=i + 1)

        result = run_investigation(sess, _snapshot(), ["low_stock"], client=client)

        # Fail-stop: exactly i+1 calls (steps 0..i attempted), no agent after i.
        assert len(client.calls) == i + 1
        # The case ends in error and reports the agent that failed by NAME.
        assert result.status == "error"
        assert result.failed_step == EXPECTED_ORDER[i]
        assert result.recommendation_id is None
    finally:
        sess.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass
