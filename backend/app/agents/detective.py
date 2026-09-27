"""Detective agent — the first step in the investigation sequence.

The Detective investigates *why* a flagged SKU is in its detected condition and
explains the likely cause. It proposes no action; that is deferred to the
Strategy and Manager agents downstream.

Like every agent in this package, it lives entirely on the read-only side of the
safety boundary. It receives an :class:`~app.agents.context.AgentContext` — plain
data only, no database handle — makes exactly one LLM call, and returns an
:class:`~app.agents.context.AgentResult` carrying its written analysis as inert
text. It has no capability to read or mutate the Inventory_Store (Req 4.3, 9.1,
9.2).

Requirements: 4.1 (produce a written investigation of the detected condition),
4.2 (base it on the SKU record and Detection_Engine classifications), 4.3 (never
modify a SKU record or the Inventory_Store).
"""

from __future__ import annotations

from app.agents.context import AgentContext, AgentResult
from app.agents.llm_client import LLMClient
from app.agents.prompts import format_sku_context

#: Stable step name used by the orchestrator to key this agent's output in
#: ``AgentContext.prior`` and to record the failed step on error.
NAME = "detective"

SYSTEM_PROMPT = (
    "You are the Detective agent in STOCKY, an inventory investigation system. "
    "You investigate why a flagged SKU is in its detected condition. Explain the "
    "likely cause of the detected condition, grounding your reasoning in the SKU "
    "record and the detection classifications you are given. Do not propose "
    "actions or recommendations — that is another agent's job. Respond with a "
    "concise written investigation in plain prose."
)


def run(context: AgentContext, *, client: LLMClient) -> AgentResult:
    """Run the Detective's single LLM call over ``context``.

    Args:
        context: The plain-data investigation context (SKU snapshot +
            classifications). No prior agent output is required for this,
            the first step.
        client: The injected LLM client. Injected rather than constructed here
            so tests can supply a stub and avoid real network calls.

    Returns:
        An :class:`AgentResult` whose ``text`` is the investigation. No ``data``
        or ``action`` is produced — the Detective only writes prose (Req 4.1).

    Raises:
        LLMError: Propagated from the LLM client on API failure so the
            orchestrator can fail-stop and mark this step failed (Req 4.4).
    """
    user_prompt = (
        "A SKU has been flagged by the deterministic detection engine. "
        "Investigate why it is in this condition and explain the likely cause.\n\n"
        f"{format_sku_context(context)}"
    )
    text = client.call(SYSTEM_PROMPT, user_prompt)
    return AgentResult(text=text)
