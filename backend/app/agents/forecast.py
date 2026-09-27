"""Forecast agent — the second step in the investigation sequence.

The Forecast agent projects near-term demand and stock trajectory for a flagged
SKU, building on the Detective's investigation. It produces a written projection
only; it proposes no action.

Like every agent in this package, it lives on the read-only side of the safety
boundary: it receives an :class:`~app.agents.context.AgentContext` (plain data,
no DB handle), makes exactly one LLM call, and returns an
:class:`~app.agents.context.AgentResult` of inert text. It cannot mutate the
Inventory_Store (Req 5.3, 9.1, 9.2).

Requirements: 5.1 (invoked with the SKU record, classifications, and Detective
output), 5.2 (produce a projection of future demand and stock trajectory), 5.3
(never modify a SKU record or the Inventory_Store).
"""

from __future__ import annotations

from app.agents.context import AgentContext, AgentResult
from app.agents.detective import NAME as DETECTIVE
from app.agents.llm_client import LLMClient
from app.agents.prompts import format_prior_outputs, format_sku_context

#: Stable step name used by the orchestrator to key this agent's output.
NAME = "forecast"

SYSTEM_PROMPT = (
    "You are the Forecast agent in STOCKY, an inventory investigation system. "
    "Given the SKU record, its detection classifications, and the Detective's "
    "investigation, project the SKU's near-term demand and stock trajectory. "
    "Describe how stock is likely to move over the coming period and when, if "
    "ever, it is likely to run out or pile up. Do not propose actions. Respond "
    "with a concise written projection in plain prose."
)


def run(context: AgentContext, *, client: LLMClient) -> AgentResult:
    """Run the Forecast agent's single LLM call over ``context``.

    Args:
        context: Investigation context carrying the SKU snapshot,
            classifications, and the Detective's prior output.
        client: Injected LLM client (injected for testability).

    Returns:
        An :class:`AgentResult` whose ``text`` is the demand/trajectory
        projection (Req 5.2). No ``data`` or ``action`` is produced.

    Raises:
        LLMError: Propagated on API failure so the orchestrator can fail-stop
            and mark this step failed (Req 5.4).
    """
    prior = format_prior_outputs(context, [DETECTIVE])
    user_prompt = (
        "Project the near-term demand and stock trajectory for this SKU, "
        "using the investigation below as context.\n\n"
        f"{format_sku_context(context)}\n\n"
        f"{prior}"
    )
    text = client.call(SYSTEM_PROMPT, user_prompt)
    return AgentResult(text=text)
