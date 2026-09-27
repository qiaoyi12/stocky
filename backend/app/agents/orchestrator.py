"""Sequential agent orchestrator — the read-only investigation runner.

This module coordinates the four LLM agents in a *fixed* order —
Detective → Forecast → Strategy → Manager — chaining each agent's output forward
as input to the next (Req 8.1, 8.2). It is deterministic Python glue: a short
loop over agent modules, one LLM call inside each step, no agent framework
(Req 8.4).

**Safety boundary (Req 9.1, 9.2).** The orchestrator lives on the read-only side
of STOCKY's safety boundary. It deliberately imports only ``case_repo`` and
``recommendation_repo`` — repositories that record an investigation case and a
*pending* recommendation, which are proposals, not inventory state. It does
**not** import ``app.repositories.sku_repo`` write functions and does **not**
import ``app.services.apply_action``; those are the only paths that mutate the
Inventory_Store and they run solely after reviewer approval. Agents receive only
an :class:`~app.agents.context.AgentContext` (plain data), so no step here has a
handle capable of writing SKU state.

**Fail-stop (Req 4.4, 5.4, 6.4, 7.5, 8.3).** If any agent raises
:class:`~app.agents.llm_client.LLMError`, the sequence stops immediately, the
failed step is recorded on the case (status ``error``), no later agent runs, and
a :class:`CaseResult` with ``status="error"`` and the ``failed_step`` name is
returned.

**On success (Req 7.3, 8.1).** After all four steps complete, the case is marked
``complete`` and exactly one recommendation is persisted with status ``pending``
from the Manager's validated :class:`~app.agents.context.ProposedAction`, using
the Manager's raw text as the rationale.

Requirements: 4.4, 5.4, 6.4, 7.3, 7.5, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy.orm import Session

from app.agents import detective, forecast, manager, strategy
from app.agents.context import ActionKind, AgentContext, ProposedAction, SkuSnapshot
from app.agents.llm_client import LLMClient, LLMError
from app.repositories import case_repo, recommendation_repo

# Fixed orchestration order (Req 8.1). Each entry is an agent module exposing a
# stable ``NAME`` and a ``run(context, *, client) -> AgentResult`` callable. The
# Manager runs last and is the only step that yields a ProposedAction.
STEPS = [detective, forecast, strategy, manager]

#: The Manager step's stable name — its output carries the ProposedAction and is
#: the source of the persisted recommendation.
_MANAGER_STEP = manager.NAME


@dataclass(frozen=True)
class CaseResult:
    """Outcome of an investigation run.

    - ``status``: ``"complete"`` when all four agents ran and a pending
      recommendation was persisted, or ``"error"`` when a step failed.
    - ``case_id``: the id of the case row created for this run (always set).
    - ``failed_step``: on ``"error"``, the ``NAME`` of the agent that failed
      (``None`` on success).
    - ``recommendation_id``: on ``"complete"``, the id of the persisted pending
      recommendation (``None`` on error).
    """

    status: str
    case_id: int
    failed_step: Optional[str] = None
    recommendation_id: Optional[int] = None


def run_investigation(
    session: Session,
    sku_snapshot: SkuSnapshot,
    classifications: List[str],
    *,
    client: LLMClient,
) -> CaseResult:
    """Run the fixed four-agent investigation sequence for one SKU.

    Creates a ``running`` case, then invokes Detective → Forecast → Strategy →
    Manager in order, chaining each agent's output forward via
    :meth:`AgentContext.with_output` and recording each step's text through
    ``case_repo.save_step`` (Req 8.1, 8.2). Exactly one LLM call happens inside
    each step.

    On the first :class:`LLMError`, the sequence fail-stops: the case is marked
    failed at that step (status ``error``), no later agent runs, and a
    ``CaseResult`` with ``status="error"`` and the failed step name is returned
    (Req 4.4, 5.4, 6.4, 7.5, 8.3).

    On success, the case is marked ``complete`` and a single ``pending``
    recommendation is persisted from the Manager's validated ``ProposedAction``,
    with the Manager's raw text as the rationale (Req 7.3).

    Args:
        session: The database session. Used only for case/recommendation
            records — never for SKU/sales inventory writes (safety boundary).
        sku_snapshot: An immutable, plain-data snapshot of the SKU under
            investigation. Agents receive this via ``AgentContext``; it grants
            no write path to the store.
        classifications: The Detection_Engine labels attached to the SKU.
        client: The LLM client each agent uses for its single call.

    Returns:
        A :class:`CaseResult` describing the outcome.
    """
    case = case_repo.create(session, sku=sku_snapshot.sku, status="running")

    ctx = AgentContext(
        sku=sku_snapshot,
        classifications=list(classifications),
        prior={},
    )

    for step in STEPS:
        try:
            result = step.run(ctx, client=client)  # exactly one LLM call inside
        except LLMError as exc:
            # Fail-stop: record the failed step, return error, run no later agent
            # (Req 4.4, 5.4, 6.4, 7.5, 8.3).
            case_repo.mark_failed(
                session, case_id=case.id, step=step.NAME, error=str(exc)
            )
            return CaseResult(
                status="error", case_id=case.id, failed_step=step.NAME
            )

        case_repo.save_step(session, case_id=case.id, step=step.NAME, text=result.text)
        ctx = ctx.with_output(step.NAME, result)  # chain output forward (Req 8.2)

    # All four steps ran. The Manager step carries the validated ProposedAction.
    manager_result = ctx.prior[_MANAGER_STEP]
    action = manager_result.action
    if action is None:
        # Defensive: the Manager always returns an action (NO_ACTION fallback at
        # worst). If somehow absent, record a no-op targeting this SKU so a
        # recommendation is still persisted rather than raising.
        action = ProposedAction(kind=ActionKind.NO_ACTION, sku=sku_snapshot.sku)

    case_repo.mark_complete(session, case_id=case.id)

    recommendation = recommendation_repo.create_pending(
        session,
        case_id=case.id,
        sku=action.sku,
        action_kind=action.kind.value,
        rationale=manager_result.text,
        quantity=action.quantity,
        new_reorder_point=action.new_reorder_point,
    )

    return CaseResult(
        status="complete",
        case_id=case.id,
        recommendation_id=recommendation.id,
    )
