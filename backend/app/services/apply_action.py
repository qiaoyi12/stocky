"""Deterministic Apply-Action Service — the SOLE inventory mutator (post-approval).

This module is the single component in STOCKY permitted to translate an approved
recommendation into an inventory mutation. It is the *only* service that imports
the inventory-write functions from ``sku_repo`` (the deterministic ingest loader
is the one other write path; agents and the orchestrator never touch them). That
isolation is the linchpin of the safety boundary (design "The Safety Boundary";
Req 9.3, 11.1) and is enforced by the import-graph test.

``apply`` is gated, deterministic, and idempotent:

* **Gating (Req 11.1):** it refuses to touch inventory unless the recommendation
  status is ``approved``. A ``pending``/``rejected`` recommendation is refused
  with the current status returned and no mutation performed.
* **Idempotence (Req 11.4):** re-applying an already-``applied`` recommendation is
  a no-op success — the inventory is not mutated a second time. The action runs
  at most once per recommendation.
* **Deterministic dispatch:** a small closed switch on ``action_kind`` maps to the
  matching ``sku_repo`` writer, affecting only the target SKU. ``reorder`` adds
  ``quantity`` to ``current_stock``; ``adjust_reorder`` sets ``reorder_point``;
  ``markdown`` and ``no_action`` record the decision without any stock change.
* **Success (Req 11.2):** on a successful write the status becomes ``applied`` and
  ``applied_at`` is stamped.
* **Failure (Req 11.3):** if the write raises (e.g. unknown SKU, DB error) the
  status is left at ``approved`` and an error message is returned so the reviewer
  can retry.

The ``approved -> applied`` transition is validated through ``services/review.py``
so lifecycle legality lives in one place; the actual inventory write happens here.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.repositories import recommendation_repo

# The ONLY import of inventory-write functions outside the ingest loader. This is
# expected and correct: apply_action is the sole post-approval inventory mutator.
from app.repositories import sku_repo
from app.schemas import ActionKind, RecommendationStatus, ReviewResult
from app.services import review
from app.services.review import IllegalTransition, UnknownRecommendation


def apply(session: Session, recommendation_id: int) -> ReviewResult:
    """Apply an approved recommendation's inventory action deterministically.

    Loads the recommendation and, gated on its lifecycle status, applies the
    exact ``ProposedAction`` delta to the target SKU:

    * status ``applied``  -> no-op success (idempotent re-apply, Req 11.4).
    * status ``approved`` -> dispatch the action, then set status ``applied``
      (Req 11.1, 11.2); on write failure leave status ``approved`` and return an
      error (Req 11.3).
    * any other status    -> refuse without mutating, returning the current
      status (Req 11.1 gating; mirrors the ``approved -> applied`` legal
      transition).

    Returns a :class:`ReviewResult` (``status`` + ``applied`` + optional
    ``error``). Raises :class:`UnknownRecommendation` if the id does not resolve
    — a programming/routing error, not a lifecycle refusal.
    """
    rec = recommendation_repo.get(session, recommendation_id)
    if rec is None:
        raise UnknownRecommendation(recommendation_id)

    # Idempotence (Req 11.4): an already-applied recommendation is a no-op. The
    # inventory was mutated exactly once, when it first transitioned to applied.
    if rec.status == RecommendationStatus.APPLIED.value:
        return ReviewResult(status=RecommendationStatus.APPLIED, applied=True)

    # Gating (Req 11.1): only an approved recommendation may mutate inventory.
    # Validate the approved -> applied transition centrally; a non-approved
    # status is refused with the current status retained and nothing written.
    try:
        review.transition(session, rec, RecommendationStatus.APPLIED.value)
    except IllegalTransition as exc:
        return ReviewResult(
            status=RecommendationStatus(exc.current),
            applied=False,
            error=(
                f"cannot apply recommendation in status {exc.current!r}; "
                "must be 'approved'"
            ),
        )

    # Deterministic dispatch (Req 11.1): the closed executable surface. Each
    # branch affects ONLY the target SKU (rec.sku).
    try:
        _dispatch(session, rec)
    except Exception as exc:  # unknown SKU (KeyError), DB error, etc.
        # Failure (Req 11.3): leave status at approved, return the error. No
        # partial state is committed by this service (the caller owns the
        # transaction boundary and rolls back on the returned error).
        return ReviewResult(
            status=RecommendationStatus.APPROVED,
            applied=False,
            error=f"apply failed: {exc}",
        )

    # Success (Req 11.2): mark applied and stamp applied_at.
    recommendation_repo.set_status(
        session,
        recommendation_id=recommendation_id,
        status=RecommendationStatus.APPLIED.value,
        applied=True,
    )
    return ReviewResult(status=RecommendationStatus.APPLIED, applied=True)


def _dispatch(session: Session, rec) -> None:
    """Execute the exact inventory delta for ``rec``'s action kind (target SKU only).

    A small deterministic switch — the only executable surface for agent-proposed
    actions. Unknown/empty kinds are treated as ``no_action`` fallbacks, matching
    the ``NO_ACTION`` coercion the orchestrator applies to unparseable Manager
    output (Req 9.4): nothing outside the closed set ever mutates inventory.
    """
    kind = rec.action_kind

    if kind == ActionKind.REORDER.value:
        # Increase current_stock by the proposed quantity.
        quantity = rec.quantity if rec.quantity is not None else 0
        sku_repo.apply_reorder(session, sku=rec.sku, quantity=quantity)
    elif kind == ActionKind.ADJUST_REORDER.value:
        # Set reorder_point to the proposed value.
        if rec.new_reorder_point is None:
            raise ValueError("adjust_reorder recommendation missing new_reorder_point")
        sku_repo.apply_adjust_reorder(
            session, sku=rec.sku, new_reorder_point=rec.new_reorder_point
        )
    elif kind in (ActionKind.MARKDOWN.value, ActionKind.NO_ACTION.value):
        # markdown / no_action: the decision is recorded (status -> applied by
        # the caller) but no stock/reorder-point mutation is performed. markdown
        # is a flag-only outcome for overstock/slow-movers; no_action is a pure
        # no-op. Neither changes skus / sales_history.
        return
    else:
        # Any kind outside the closed set is inert (Req 9.4): treat as no_action.
        return
