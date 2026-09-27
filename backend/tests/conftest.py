"""Shared pytest fixtures for the STOCKY backend test suite.

Provides an isolated, in-memory SQLite session so tests never touch the real
``stocky.db`` file, plus a fake LLM client so agent/orchestration tests run
fully offline and deterministically (no real network calls). Both are kept
minimal and reused across the safety-boundary and other backend tests.
"""

from __future__ import annotations

from typing import List

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.agents.context import ActionKind

# Import models for side effect: registers every table on ``Base.metadata`` so
# ``create_all`` below materialises the full schema.
from app import models  # noqa: F401


@pytest.fixture()
def session() -> Session:
    """Yield a SQLAlchemy session bound to a fresh in-memory SQLite database.

    Each test gets its own engine/schema, so state never leaks between tests
    and the real on-disk ``stocky.db`` is never touched.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


class FakeLLMClient:
    """A stand-in for :class:`app.agents.llm_client.LLMClient`.

    Returns canned text for the earlier agents and a strict-JSON Manager
    response, so an investigation runs end-to-end offline. It holds no network
    handle and performs no I/O — exactly what the safety story wants under test.

    ``call`` count is tracked so tests can assert exactly one call per agent.
    """

    def __init__(self, manager_json: str | None = None) -> None:
        self.calls: List[tuple[str, str]] = []
        self._manager_json = manager_json

    def call(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        # The Manager's system prompt asks for strict JSON; detect it to return
        # a well-formed structured response. Everything else is prose.
        if "Manager agent" in system_prompt:
            if self._manager_json is not None:
                return self._manager_json
            # A safe, well-formed no_action recommendation by default.
            return (
                '{"action_kind": "no_action", "sku": "SKU-1", '
                '"rationale": "Analysis only; no change warranted."}'
            )
        return "Deterministic canned analysis text for testing."
