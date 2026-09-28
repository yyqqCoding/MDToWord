"""Trusted terminal notification client for the local notification MCP service."""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol
from uuid import UUID

import httpx

from agent.controller import GateRunOutcome
from agent.domain.enums import TERMINAL_RUN_STATUSES

_LOGGER = logging.getLogger(__name__)


class EmailMcpNotifier:
    """Calls the local MCP bridge after a durable terminal Agent outcome.

    The recipient and sender are server-side MCP configuration; they are never
    accepted from the outcome or model input.
    """

    def __init__(self, *, endpoint: str, token: str, client: httpx.AsyncClient,
                 timeout_seconds: float = 10.0, retries: int = 1) -> None:
        if not endpoint.startswith("http://127.0.0.1"):
            raise ValueError("notification MCP endpoint must be local HTTP")
        if not token:
            raise ValueError("notification MCP token must not be empty")
        self._endpoint = endpoint.rstrip("/")
        self._token = token
        self._client = client
        self._timeout_seconds = timeout_seconds
        self._retries = retries

    async def on_run_settled(self, outcome: GateRunOutcome) -> None:
        if outcome.status not in TERMINAL_RUN_STATUSES:
            return
        notification_id = f"{outcome.run_id}:{outcome.status.value}:email"
        payload = {
            "notification_id": notification_id,
            "run_ref": str(outcome.run_id),
            "status": outcome.status.value,
            "route": outcome.route.value if outcome.route else None,
            "category": None,
            "pr_url": outcome.pr_url,
            "issue_url": outcome.issue_url,
            "trace_url": None,
            "failure_code": outcome.error_code,
        }
        for attempt in range(self._retries + 1):
            try:
                response = await self._client.post(
                    f"{self._endpoint}/tools/send_email",
                    json=payload,
                    headers={"authorization": f"Bearer {self._token}"},
                    timeout=self._timeout_seconds,
                )
                if response.status_code < 500 and response.status_code != 429:
                    if response.status_code >= 400:
                        _LOGGER.warning("notification MCP rejected request: %s", response.status_code)
                    return
            except Exception as exc:
                if attempt >= self._retries:
                    _LOGGER.warning("notification MCP call failed: %s", type(exc).__name__)
                    return
            if attempt < self._retries:
                await asyncio.sleep(1)


class CompositeRunSettledListener:
    """Fan out terminal notifications without letting one sink block another."""

    def __init__(self, *listeners: Protocol) -> None:
        self._listeners = tuple(item for item in listeners if item is not None)

    async def on_run_settled(self, outcome: GateRunOutcome) -> None:
        for listener in self._listeners:
            try:
                await listener.on_run_settled(outcome)
            except Exception as exc:  # pragma: no cover - defensive boundary
                _LOGGER.warning("terminal listener failed: %s", type(exc).__name__)
