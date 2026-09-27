"""Deterministic backend services for STOCKY.

This package holds the non-agent, deterministic business logic that sits behind
the safety boundary: recommendation lifecycle validation (``review``) and — in a
later task — the sole inventory-mutating apply-action service. Agent modules must
never import from this package's write paths.
"""
