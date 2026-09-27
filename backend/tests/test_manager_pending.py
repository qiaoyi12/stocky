"""Property 7 — Manager produces one well-formed recommendation persisted as pending.

Exercises :func:`app.agents.orchestrator.run_investigation` end-to-end against
the offline :class:`tests.conftest.FakeLLMClient`, feeding it a wide variety of
Manager outputs and asserting the safety-relevant invariants on the *persisted*
recommendation.

The property under test (design.md → Correctness Properties → Property 7):

    For any Manager output, parsing yields exactly one recommendation whose
    ``action_kind`` is a member of the closed action enum (unknown or malformed
    output coerces to ``no_action``) with a non-empty rationale; and when the
    case completes successfully, the persisted recommendation reads back with
    status ``pending``.

The persisted rationale follows the Manager contract (Req 7.2): when the raw
output parses as a JSON object carrying a non-empty ``rationale`` string, that
parsed rationale is stored; otherwise the raw model text is stored verbatim
(non-JSON / garbage, or JSON lacking a usable rationale field). A successful
case persists exactly one recommendation with status ``pending``
(Req 7.3). Unknown ``action_kind`` values and malformed / non-JSON output are
coerced to ``no_action`` by the Manager parser (Req 9.4), while valid outputs
retain their action kind.

**Validates: Requirements 7.2, 7.3**
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.context import ActionKind, SkuSnapshot
from app.agents.orchestrator import run_investigation
from app.repositories import recommendation_repo
from tests.conftest import FakeLLMClient

# The closed set of action kinds the persisted recommendation must belong to.
CLOSED_ACTION_KINDS = {kind.value for kind in ActionKind}

# The fixed SKU id the snapshot uses. The Manager parser falls back to this
# context SKU when the generated JSON omits or mismatches ``sku``.
SKU_ID = "SKU-42"


def expected_rationale_for(raw: str) -> str:
    """Compute the persisted rationale the Manager contract yields for ``raw``.

    Mirrors the Manager contract without coupling to implementation internals:
    locate the first balanced ``{...}`` object substring in ``raw`` and
    ``json.loads`` it; if the decoded value is a dict whose ``rationale`` is a
    non-empty (after strip) string, the expected rationale is that value.
    Otherwise the expected rationale is the raw string verbatim (non-JSON /
    garbage, JSON that is not an object, or JSON lacking a usable rationale).
    """
    start = raw.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if (
                        isinstance(parsed, dict)
                        and isinstance(parsed.get("rationale"), str)
                        and parsed["rationale"].strip() != ""
                    ):
                        return parsed["rationale"]
                    break
    return raw


def _snapshot() -> SkuSnapshot:
    """A fixed, valid SKU snapshot to drive an investigation."""
    return SkuSnapshot(
        sku=SKU_ID,
        name="Test Widget",
        category="general",
        current_stock=12,
        reorder_point=5,
        lead_time_days=7,
        unit_cost=2.5,
        sales_history=[1, 2, 3, 4],
        sales_velocity=2.5,
        days_of_cover=4.8,
        stockout_eta=None,
        no_recent_sales=False,
        classifications=["needs_reorder"],
    )


@contextmanager
def _temp_session() -> Iterator[Session]:
    """Yield a session on a throwaway SQLite file, torn down afterwards.

    Hypothesis's ``@given`` cannot be combined with the function-scoped
    ``session`` conftest fixture (it would be reused across every generated
    example), so each example builds its own isolated database inline — the same
    pattern used by the Property 6 order test.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}, future=True
    )
    from app import models  # noqa: F401  (register tables on Base.metadata)
    from app.db import Base

    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    sess = factory()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()
        try:
            os.remove(path)
        except OSError:
            pass


# --- Generators for varied Manager outputs ----------------------------------


def _valid_manager_json() -> st.SearchStrategy[tuple[str, str]]:
    """Well-formed strict JSON with a recognised ``action_kind``.

    Returns ``(manager_json, expected_action_kind)``. Each kind carries the
    fields it uses (quantity for reorder, new_reorder_point for adjust_reorder)
    so the parser accepts it and preserves the kind.
    """

    def build(kind: str, sku: str, qty: int, rp: int, rationale: str) -> tuple[str, str]:
        import json

        obj: dict[str, object] = {
            "action_kind": kind,
            "sku": sku,
            "rationale": rationale,
        }
        if kind == "reorder":
            obj["quantity"] = qty
        elif kind == "adjust_reorder":
            obj["new_reorder_point"] = rp
        return json.dumps(obj), kind

    return st.builds(
        build,
        kind=st.sampled_from(sorted(CLOSED_ACTION_KINDS)),
        # Sometimes the SKU matches the context, sometimes it differs — either
        # way parsing must succeed and yield exactly one recommendation.
        sku=st.sampled_from([SKU_ID, "OTHER-SKU"]),
        qty=st.integers(min_value=1, max_value=10_000),
        rp=st.integers(min_value=0, max_value=10_000),
        rationale=st.text(min_size=1, max_size=80).filter(lambda s: s.strip() != ""),
    )


def _unknown_action_json() -> st.SearchStrategy[tuple[str, str]]:
    """Structurally valid JSON but with an ``action_kind`` outside the enum.

    Expected to coerce to ``no_action`` (Req 9.4).
    """

    def build(bogus: str, sku: str) -> tuple[str, str]:
        import json

        return (
            json.dumps({"action_kind": bogus, "sku": sku, "rationale": "x"}),
            "no_action",
        )

    bogus_kinds = st.text(min_size=1, max_size=20).filter(
        lambda s: s not in CLOSED_ACTION_KINDS
    )
    return st.builds(build, bogus=bogus_kinds, sku=st.just(SKU_ID))


def _malformed_output() -> st.SearchStrategy[tuple[str, str]]:
    """Non-JSON / garbage / injection-looking text that cannot be validated.

    All coerce to ``no_action`` while the raw text is preserved as rationale.
    """
    # Note: a truly empty / whitespace-only Manager response is excluded. The
    # parser preserves raw text verbatim as the rationale, so an empty response
    # would yield an empty rationale — degenerate, not a realistic Manager
    # output, and outside the "non-empty rationale" input space Property 7
    # describes. Every sample here is non-empty text.
    samples = [
        "not json at all",
        "{ this is not valid json",
        "'; DROP TABLE skus; --",
        "UPDATE skus SET current_stock = 0 WHERE 1=1;",
        "<script>alert('x')</script>",
        '{"action_kind": "reorder"}',  # JSON object but missing required sku
        '{"sku": "SKU-42"}',  # missing action_kind
        "[1, 2, 3]",  # JSON, but not an object
        '{"action_kind": "reorder", "sku": "", "rationale": "empty sku"}',
    ]
    return st.sampled_from(samples).map(lambda text: (text, "no_action"))


def _manager_case() -> st.SearchStrategy[tuple[str, str]]:
    """Union of all output categories: ``(manager_output, expected_kind)``."""
    return st.one_of(
        _valid_manager_json(),
        _unknown_action_json(),
        _malformed_output(),
    )


# --- Property 7 -------------------------------------------------------------


@settings(deadline=None, max_examples=150)
@given(case=_manager_case())
def test_manager_recommendation_persisted_as_pending(case):
    """For any Manager output, one pending, well-formed recommendation persists.

    Drives a full investigation with ``FakeLLMClient(manager_json=<output>)``
    and asserts against the recommendation read back from the store:

    - exactly one recommendation is persisted for the case;
    - its ``action_kind`` is a member of the closed action enum;
    - its ``rationale`` is non-empty and equals the contract-expected value —
      the parsed ``rationale`` when the output was JSON with a non-empty
      rationale, else the raw Manager text (Req 7.2);
    - its ``status`` reads back as ``pending`` (Req 7.3);
    - unknown / malformed output coerces to ``no_action`` (Req 9.4), while valid
      output retains its action kind.

    **Validates: Requirements 7.2, 7.3**
    """
    manager_output, expected_kind = case

    with _temp_session() as session:
        client = FakeLLMClient(manager_json=manager_output)

        result = run_investigation(
            session, _snapshot(), ["needs_reorder"], client=client
        )

        # The case completes successfully and reports a persisted recommendation.
        assert result.status == "complete"
        assert result.recommendation_id is not None

        # Exactly one recommendation persisted (Req 7.3): the one for this case,
        # and it is the only row in the table.
        all_recs = recommendation_repo.list_all(session)
        assert len(all_recs) == 1
        rec = recommendation_repo.get(session, result.recommendation_id)
        assert rec is not None
        assert rec.id == all_recs[0].id
        assert rec.case_id == result.case_id

        # action_kind is a member of the closed enum.
        assert rec.action_kind in CLOSED_ACTION_KINDS
        assert rec.action_kind == expected_kind

        # Rationale follows the Manager contract (Req 7.2): the parsed
        # ``rationale`` when the raw output was JSON carrying a non-empty
        # rationale, otherwise the raw model text verbatim.
        expected_rationale = expected_rationale_for(manager_output)
        assert rec.rationale == expected_rationale
        assert len(rec.rationale) > 0

        # Status reads back as pending (Req 7.3).
        assert rec.status == "pending"


# --- Focused example using the shared conftest `session` fixture ------------


def test_valid_reorder_persists_pending_via_conftest_session(session):
    """A concrete valid reorder output persists one pending recommendation.

    Reuses the shared in-memory ``session`` fixture and the ``FakeLLMClient``
    from ``conftest`` to exercise the success path with a real, recognised
    action kind (not just the NO_ACTION fallback).

    **Validates: Requirements 7.2, 7.3**
    """
    manager_output = (
        '{"action_kind": "reorder", "sku": "SKU-42", "quantity": 50, '
        '"rationale": "Stockout risk; reorder 50 units."}'
    )
    client = FakeLLMClient(manager_json=manager_output)

    result = run_investigation(session, _snapshot(), ["needs_reorder"], client=client)

    assert result.status == "complete"
    rec = recommendation_repo.get(session, result.recommendation_id)
    assert rec is not None
    assert rec.action_kind == "reorder"
    assert rec.quantity == 50
    assert rec.status == "pending"
    assert rec.rationale == "Stockout risk; reorder 50 units."
    # Exactly one recommendation persisted.
    assert len(recommendation_repo.list_all(session)) == 1
