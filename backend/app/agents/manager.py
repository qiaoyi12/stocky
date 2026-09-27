"""Manager agent — the fourth and final step in the investigation sequence.

The Manager consolidates the Detective, Forecast, and Strategy outputs into
exactly ONE recommendation. Unlike the earlier agents, it is asked for strict
JSON: ``{action_kind, sku, quantity?, new_reorder_point?, rationale}``. That JSON
is parsed and validated into a :class:`~app.agents.context.ProposedAction` whose
``kind`` must be a member of the closed :class:`~app.agents.context.ActionKind`
enum.

This is where Requirement 9.4 is enforced. The *executable* surface is a tiny
validated schema: only a recognised ``action_kind`` and the closed set of fields
survive. Anything the model emits outside that schema — extra fields, prose
around the JSON, an unknown action, or outright malformed output — is dropped.
On any parse/validation failure the proposal is coerced to a safe ``NO_ACTION``
targeting the context SKU, and the raw model text is preserved verbatim as the
rationale (stored later as inert text, never executed).

Like every agent here, the Manager lives on the read-only side of the safety
boundary: plain-data context in, one LLM call, an :class:`AgentResult` out; it
holds no DB handle and cannot mutate the Inventory_Store (Req 7.4, 9.1, 9.2).

Requirements: 7.1 (invoked with the SKU record, classifications, and all prior
outputs), 7.2 (produce a single structured recommendation with rationale), 7.4
(never modify a SKU record or the Inventory_Store), 9.4 (mutation-encoding output
is treated as inert text, coerced to NO_ACTION).
"""

from __future__ import annotations

import json
from typing import Optional

from app.agents.context import ActionKind, AgentContext, AgentResult, ProposedAction
from app.agents.detective import NAME as DETECTIVE
from app.agents.forecast import NAME as FORECAST
from app.agents.llm_client import LLMClient
from app.agents.prompts import format_prior_outputs, format_sku_context
from app.agents.strategy import NAME as STRATEGY

#: Stable step name used by the orchestrator to key this agent's output.
NAME = "manager"

SYSTEM_PROMPT = (
    "You are the Manager agent in STOCKY, an inventory investigation system. "
    "Consolidate the Detective's investigation, the Forecast's projection, and "
    "the Strategy's options into exactly ONE recommendation for the SKU.\n\n"
    "Respond with STRICT JSON only — no prose, no markdown fences — matching:\n"
    "{\n"
    '  "action_kind": one of "reorder" | "adjust_reorder" | "markdown" | "no_action",\n'
    '  "sku": the target SKU id,\n'
    '  "quantity": integer units to reorder (only for "reorder"),\n'
    '  "new_reorder_point": integer (only for "adjust_reorder"),\n'
    '  "rationale": a short supporting explanation\n'
    "}\n\n"
    'Use "reorder" with a quantity to increase stock, "adjust_reorder" with a '
    'new_reorder_point to change the reorder threshold, "markdown" for '
    'overstock/slow-movers, or "no_action" when no change is warranted. Emit the '
    "JSON object and nothing else."
)


def run(context: AgentContext, *, client: LLMClient) -> AgentResult:
    """Run the Manager's single LLM call and validate its structured output.

    Args:
        context: Investigation context carrying the SKU snapshot,
            classifications, and all three prior agent outputs.
        client: Injected LLM client (injected for testability).

    Returns:
        An :class:`AgentResult` whose ``text`` is the parsed ``rationale``
        string when the Manager output parsed as JSON containing a non-empty
        rationale field; otherwise the raw model text (fallback, e.g.
        non-JSON/garbage). ``data`` always retains the parsed JSON object (or
        ``None``); ``action`` is the validated :class:`ProposedAction` (or a
        ``NO_ACTION`` fallback targeting this SKU) (Req 7.2, 9.4). This ``text``
        feeds BOTH the persisted recommendation rationale and the case
        ``manager_out`` box, and the raw output is never executed — the
        safety/inert-data guarantee holds, with the raw JSON retained in
        ``data``.

    Raises:
        LLMError: Propagated on API failure so the orchestrator can fail-stop
            and mark this step failed (Req 7.5). Note: a *bad* (but received)
            model response is not an error — it is coerced to NO_ACTION here.
    """
    prior = format_prior_outputs(context, [DETECTIVE, FORECAST, STRATEGY])
    user_prompt = (
        "Consolidate the analysis below into exactly one recommendation for "
        f"this SKU (sku id: {context.sku.sku}). Respond with strict JSON only.\n\n"
        f"{format_sku_context(context)}\n\n"
        f"{prior}"
    )
    raw = client.call(SYSTEM_PROMPT, user_prompt)
    data, action = _parse_manager(raw, fallback_sku=context.sku.sku)
    if (
        isinstance(data, dict)
        and isinstance(data.get("rationale"), str)
        and data["rationale"].strip() != ""
    ):
        display_text = data["rationale"]
    else:
        display_text = raw
    return AgentResult(text=display_text, data=data, action=action)


def _parse_manager(
    raw: str, *, fallback_sku: str
) -> tuple[Optional[dict], ProposedAction]:
    """Parse the Manager's raw output into a validated ``ProposedAction``.

    Attempts to extract a JSON object from ``raw``, read a recognised
    ``action_kind`` from the closed enum, and build a ``ProposedAction`` from the
    closed set of fields. Any failure — no JSON found, invalid JSON, missing
    ``sku``, or an unknown ``action_kind`` — is caught and coerced to a safe
    ``NO_ACTION`` proposal targeting ``fallback_sku`` (Req 9.4). The raw text is
    always preserved by the caller as the rationale.

    Returns:
        A tuple of ``(parsed_json_or_None, proposed_action)``. ``parsed_json`` is
        the decoded object when JSON decoding succeeded (even if validation then
        failed), so the raw structured data is retained as inert data; it is
        ``None`` when no JSON could be decoded.
    """
    data: Optional[dict] = None
    try:
        candidate = _extract_json(raw)
        parsed = json.loads(candidate)
        if not isinstance(parsed, dict):
            raise ValueError("Manager JSON was not an object")
        data = parsed

        kind = ActionKind(parsed["action_kind"])  # unknown value -> ValueError
        sku = parsed["sku"]
        if not isinstance(sku, str) or not sku:
            raise ValueError("Manager JSON 'sku' must be a non-empty string")

        action = ProposedAction(
            kind=kind,
            sku=sku,
            quantity=_coerce_optional_int(parsed.get("quantity")),
            new_reorder_point=_coerce_optional_int(parsed.get("new_reorder_point")),
        )
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        # Anything unparseable/invalid falls back to a safe no-op. The raw text
        # survives as the rationale; nothing is executed (Req 9.4).
        action = ProposedAction(kind=ActionKind.NO_ACTION, sku=fallback_sku)

    return data, action


def _extract_json(raw: str) -> str:
    """Extract the first top-level JSON object substring from ``raw``.

    The model may wrap the object in prose or markdown fences despite the strict
    instruction. This slices from the first ``{`` to its matching ``}`` (tracking
    brace depth, ignoring braces inside strings). Raises ``ValueError`` when no
    balanced object is found so the caller coerces to NO_ACTION.
    """
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found in Manager output")

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
                return raw[start : i + 1]

    raise ValueError("No balanced JSON object found in Manager output")


def _coerce_optional_int(value: object) -> Optional[int]:
    """Coerce an optional numeric field to ``int``, or ``None``.

    Accepts ``None`` (field absent) and integer-valued numbers. A non-numeric or
    non-integral value raises so the whole proposal coerces to NO_ACTION rather
    than silently carrying a nonsense quantity into the executable schema.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        # bool is an int subclass; a boolean quantity is not meaningful.
        raise ValueError("numeric field must not be a boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ValueError("numeric field must be an integer")
