"""Strategy agent — the third step in the investigation sequence.

The Strategy agent proposes candidate courses of action for a flagged SKU,
listing options with their trade-offs. It deliberately does *not* pick one —
consolidating into a single recommendation is the Manager's job.

Like every agent in this package, it lives on the read-only side of the safety
boundary: it receives an :class:`~app.agents.context.AgentContext` (plain data,
no DB handle), makes exactly one LLM call, and returns an
:class:`~app.agents.context.AgentResult` of inert text. It cannot mutate the
Inventory_Store (Req 6.3, 9.1, 9.2).

Requirements: 6.1 (invoked with the SKU record, classifications, and prior
outputs), 6.2 (produce one or more candidate courses of action), 6.3 (never
modify a SKU record or the Inventory_Store).
"""

from __future__ import annotations

from app.agents.context import AgentContext, AgentResult
from app.agents.detective import NAME as DETECTIVE
from app.agents.forecast import NAME as FORECAST
from app.agents.llm_client import LLMClient
from app.agents.prompts import format_prior_outputs, format_sku_context

#: Stable step name used by the orchestrator to key this agent's output.
NAME = "strategy"

SYSTEM_PROMPT = (
    "You are the Strategy agent in STOCKY, an inventory investigation system. "
    "Given the SKU record, its detection classifications, the Detective's "
    "investigation, and the Forecast's projection, propose candidate courses of "
    "action. Provide 2 to 3 distinct candidate options. For each option give a "
    "short name/label, the action it entails, a recommended supplier (from the "
    "SKU's supplier_name), a recommended timing for the action (e.g. 'reorder "
    "now' vs 'reorder in N days'), informed by lead_time_days and the Forecast "
    "agent's demand projection, and its key trade-off (cost, risk, benefit), in "
    "2-3 concise sentences. Do not pick a single option — presenting the "
    "choices is your job; the Manager decides. Respond in plain prose."
)


def run(context: AgentContext, *, client: LLMClient) -> AgentResult:
    """Run the Strategy agent's single LLM call over ``context``.

    Args:
        context: Investigation context carrying the SKU snapshot,
            classifications, and the Detective and Forecast prior outputs.
        client: Injected LLM client (injected for testability).

    Returns:
        An :class:`AgentResult` whose ``text`` lists candidate options with
        trade-offs (Req 6.2). No ``data`` or ``action`` is produced.

    Raises:
        LLMError: Propagated on API failure so the orchestrator can fail-stop
            and mark this step failed (Req 6.4).
    """
    prior = format_prior_outputs(context, [DETECTIVE, FORECAST])
    user_prompt = (
        "Propose one or more candidate courses of action for this SKU, with "
        "trade-offs for each. Use the investigation and projection below as "
        "context.\n\n"
        f"{format_sku_context(context)}\n\n"
        f"{prior}"
    )
    text = client.call(SYSTEM_PROMPT, user_prompt)
    return AgentResult(text=text)
