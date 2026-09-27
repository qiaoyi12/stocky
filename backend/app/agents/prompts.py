"""Shared user-prompt rendering helpers for the agent layer.

Each agent turns its plain-data :class:`~app.agents.context.AgentContext` into a
user-message string for its single LLM call. The rendering is factored here so
every agent presents the SKU snapshot and prior agent outputs the same way, and
so the chaining semantics (each agent's text appended and shown to the next —
Req 8.2) are expressed in one place.

This module is data-only and read-only: it formats text from an already-frozen
context and never touches the Inventory_Store.
"""

from __future__ import annotations

from typing import Iterable

from app.agents.context import AgentContext


def format_sku_context(context: AgentContext) -> str:
    """Render the SKU snapshot and its classifications as prompt text.

    Every agent needs the SKU facts and the Detection_Engine labels (Req 4.2,
    5.1, 6.1, 7.1), so this block is shared. Optional metrics that are
    ``None`` for the zero-velocity edge (Req 2.5) are rendered as "n/a" rather
    than omitted, so the model sees they are explicitly undefined.
    """
    sku = context.sku
    classifications = context.classifications or sku.classifications
    labels = ", ".join(classifications) if classifications else "none"

    velocity = _fmt(sku.sales_velocity)
    days_of_cover = _fmt(sku.days_of_cover)
    stockout_eta = sku.stockout_eta if sku.stockout_eta is not None else "n/a"
    last_sold_date = sku.last_sold_date if sku.last_sold_date is not None else "n/a"

    lines = [
        "SKU RECORD",
        f"- sku: {sku.sku}",
        f"- name: {sku.name}",
        f"- category: {sku.category}",
        f"- current_stock: {sku.current_stock}",
        f"- reorder_point: {sku.reorder_point}",
        f"- lead_time_days: {sku.lead_time_days}",
        f"- unit_cost: {sku.unit_cost}",
        f"- selling_price: {sku.selling_price}",
        f"- supplier_name: {sku.supplier_name}",
        f"- avg_daily_sales: {sku.avg_daily_sales}",
        f"- last_sold_date: {last_sold_date}",
        f"- recent_sales_history: {sku.sales_history}",
        f"- sales_velocity (units/day): {velocity}",
        f"- days_of_cover: {days_of_cover}",
        f"- stockout_eta: {stockout_eta}",
        f"- no_recent_sales: {sku.no_recent_sales}",
        "",
        "DETECTION CLASSIFICATIONS",
        f"- {labels}",
    ]
    return "\n".join(lines)


def format_prior_outputs(context: AgentContext, steps: Iterable[str]) -> str:
    """Render the named prior agent outputs, in order, for the next agent.

    Chains each completed agent's written output forward as input to the next
    agent (Req 5.1, 6.1, 7.1, 8.2). Steps that are absent from ``context.prior``
    are skipped rather than erroring, keeping this tolerant if a caller lists a
    step that has not run.
    """
    blocks = []
    for step in steps:
        result = context.prior.get(step)
        if result is None:
            continue
        blocks.append(f"{step.upper()} AGENT OUTPUT\n{result.text}")
    return "\n\n".join(blocks)


def _fmt(value: float | None) -> str:
    """Format an optional numeric metric, showing undefined values as "n/a"."""
    return "n/a" if value is None else str(value)
