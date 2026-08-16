"""Optional, bounded narrative synthesis. Deterministic decisions always remain authoritative."""
from __future__ import annotations

import os
from typing import Any, Protocol


class DecisionSynthesizer(Protocol):
    def summarize(self, snapshot: dict[str, Any], decision: dict[str, Any]) -> str | None: ...


class DisabledSynthesizer:
    def summarize(self, snapshot: dict[str, Any], decision: dict[str, Any]) -> str | None:
        return None


def configured_synthesizer() -> DecisionSynthesizer:
    """Graph invocation is deliberately not wired into request handling yet.

    TradingAgentsGraph changes process-global dataflow configuration, so directly
    invoking it per chat request would introduce cross-request configuration races.
    A dedicated isolated worker/service can implement this protocol later.
    """
    if os.getenv("TRADINGAGENTS_CHAT_GRAPH_SYNTHESIS", "false").lower() in {"1", "true", "yes", "on"}:
        # Do not pretend the graph is available: server-side credentials and an
        # isolated execution boundary are required before enabling it.
        return DisabledSynthesizer()
    return DisabledSynthesizer()
