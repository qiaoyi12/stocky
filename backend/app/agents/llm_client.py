"""LLM client wrapper for STOCKY agents.

A thin, synchronous ``httpx`` wrapper around the external LLM API. Each agent
(Detective, Forecast, Strategy, Manager) makes exactly one ``call()`` here,
passing a role system prompt plus a user prompt and receiving the model's text
response back.

This module belongs to the **read-only** side of the safety boundary: it holds
no database handle and performs no inventory writes. It only ever sends prompts
to the external LLM and returns text. Any failure talking to the API — network
error, non-2xx status, or an unparseable response body — is surfaced as a typed
:class:`LLMError` so the orchestrator can record the failed step and return an
``error`` case status (Requirements 4.4, 5.4, 6.4, 7.5).

Model, API key, and base URL are read from :mod:`app.config` (``LLM_MODEL``,
``LLM_API_KEY``, ``LLM_API_BASE``), which in turn reads them from the
environment. No credentials are hard-coded here.
"""

from __future__ import annotations

import httpx

from app import config


class LLMError(Exception):
    """Raised when a call to the external LLM API cannot be completed.

    Covers every failure mode of a single ``call()``: transport/network
    errors, non-success HTTP status codes, and malformed response payloads.
    The orchestrator catches this to fail-stop the agent sequence and mark the
    offending step as failed (Requirements 4.4, 5.4, 6.4, 7.5).
    """


# Default timeout (seconds) for a single LLM HTTP request. A single agent call
# should not hang the whole investigation indefinitely; a timeout surfaces as an
# LLMError like any other transport failure.
DEFAULT_TIMEOUT_SECONDS: float = 60.0


class LLMClient:
    """Synchronous ``httpx`` wrapper exposing one :meth:`call` per agent.

    Configuration (model, API key, base URL) is drawn from :mod:`app.config`
    by default so agents need no configuration knowledge, but the values may be
    overridden per-instance (useful for tests that must avoid real network
    calls). ``max_tokens`` defaults from :data:`config.LLM_MAX_TOKENS`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.LLM_API_KEY
        self.api_base = api_base if api_base is not None else config.LLM_API_BASE
        self.model = model if model is not None else config.LLM_MODEL
        self.timeout = timeout
        self.max_tokens = max_tokens if max_tokens is not None else config.LLM_MAX_TOKENS

    def call(self, system_prompt: str, user_prompt: str) -> str:
        """Send one chat completion request and return the response text.

        Args:
            system_prompt: The agent's role/system message.
            user_prompt: The user message (SKU context + prior agent outputs).

        Returns:
            The assistant message content as plain text.

        Raises:
            LLMError: If the request cannot be sent, the API returns a non-2xx
                status, or the response body cannot be parsed into a text
                completion. The raw output is never executed; it is only ever
                returned as text (Requirement 9.4).
        """
        url = self._chat_completions_url()
        headers = {
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": self.max_tokens,
        }

        try:
            response = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise LLMError(f"LLM API returned error status {status}") from exc
        except httpx.HTTPError as exc:
            # Covers connect/read timeouts, connection errors, etc.
            raise LLMError(f"LLM API request failed: {exc}") from exc
        except ValueError as exc:
            # response.json() raised on a non-JSON body.
            raise LLMError("LLM API returned a non-JSON response body") from exc

        return self._extract_text(data)

    def _chat_completions_url(self) -> str:
        """Build the chat-completions endpoint URL from the configured base.

        The organiser gateway is an Ollama server whose OpenAI-compatible API
        lives under ``/v1`` (e.g. ``https://api.softwaresystems.app`` ->
        ``https://api.softwaresystems.app/v1/chat/completions``). We normalize
        here rather than mutating the configured base:

        - strip trailing slashes;
        - if the base does not already end with ``/v1`` and does not already
          contain ``/chat/completions``, append ``/v1`` (so a base already set
          to ``.../v1`` is not doubled);
        - append ``/chat/completions``.
        """
        base = (self.api_base or "").rstrip("/")
        if not base.endswith("/v1") and "/chat/completions" not in base:
            base = f"{base}/v1"
        return f"{base}/chat/completions"

    @staticmethod
    def _extract_text(data: object) -> str:
        """Pull the assistant message text out of an OpenAI-style response.

        Raises:
            LLMError: If the payload is not shaped as expected.
        """
        try:
            choices = data["choices"]  # type: ignore[index]
            message = choices[0]["message"]
            content = message["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("LLM API response missing completion content") from exc

        if not isinstance(content, str):
            raise LLMError("LLM API completion content was not text")

        return content
