from __future__ import annotations
from typing import Any

def classify(states: dict[str, dict[str, Any]]) -> str:
    biases=[s.get("bias") for s in states.values()]
    if biases.count("bullish")>=3: return "TRENDING_UP"
    if biases.count("bearish")>=3: return "TRENDING_DOWN"
    return "RANGING_OR_MIXED"
