"""Application configuration for STOCKY.

Holds environment-derived settings (LLM API credentials/target) and the
deterministic Detection_Engine thresholds. No LLM is ever invoked here — these
values are pure configuration consumed by the pure detection functions
(Requirement 2.4).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env at import time using an explicit path so it resolves
# regardless of the current working directory. This file (config.py) lives at
# backend/app/config.py, so the backend root is two parents up.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# --- Detection_Engine thresholds (defaults chosen for demo data) ------------
# units/day above which a SKU is classified fast-moving (Req 3.3)
FAST_MOVING_THRESHOLD: float = 20.0
# units/day at or below which a SKU is classified slow-moving (Req 3.4)
SLOW_MOVING_THRESHOLD: float = 1.0
# days-of-cover above which a SKU is classified overstock (Req 3.5)
OVERSTOCK_DOC_THRESHOLD: float = 90.0
# relative directional change between window halves that flags a trend/anomaly (Req 3.6)
ANOMALY_REL_THRESHOLD: float = 0.5

# --- Sales history window ---------------------------------------------------
# default window length (days) used when a window size is not otherwise known
SALES_WINDOW_DAYS: int = 14

# --- Database ---------------------------------------------------------------
DATABASE_URL: str = os.environ.get("DATABASE_URL", "sqlite:///./stocky.db")

# --- External LLM API (read from environment) -------------------------------
# The organiser-provided gateway is an Ollama server exposing an
# OpenAI-compatible API at ``/v1`` (POST /v1/chat/completions). ``LLM_PROVIDER``
# is informational for now; the single OpenAI-compatible client in
# ``app.agents.llm_client`` is always used.
LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "gateway")

# Accept both naming schemes: the generic LLM_API_* names take precedence, with
# the LLM_GATEWAY_* names the user entered as fallbacks. No secret is logged.
LLM_API_BASE: str = os.environ.get("LLM_API_BASE") or os.environ.get("LLM_GATEWAY_URL", "")
LLM_API_KEY: str = os.environ.get("LLM_API_KEY") or os.environ.get("LLM_GATEWAY_API_KEY", "")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "")
# Max output tokens per LLM call; raise to avoid truncated agent responses.
LLM_MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "1024"))

# NOTE: the OpenAI-compatible base for this Ollama gateway needs a ``/v1``
# suffix. That normalization happens in the client's ``_chat_completions_url``
# (app.agents.llm_client), not by mutating the configured base value here.
