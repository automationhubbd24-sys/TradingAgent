"""Bounded hybrid intelligence pipeline; it never executes trades or runs TradingAgentsGraph."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from tradingagents.llm_clients.factory import create_llm_client
from .foundation import Store, decide

FINAL_STATES = {"LONG_READY", "LONG_WAIT", "SHORT_READY", "SHORT_WAIT", "NO_TRADE", "DATA_UNAVAILABLE"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def verified_market_state(snapshot: dict[str, Any], deterministic: dict[str, Any], max_evidence: int = 40) -> dict[str, Any]:
    """Create a compact, JSON-safe state from existing v3 output only."""
    evidence: list[dict[str, Any]] = []
    freshness = snapshot.get("quality", {})
    for tf, item in deterministic.get("structure", {}).items():
        detail = f"{tf} structure={item.get('structure', 'UNKNOWN')}; bias={item.get('bias', 'neutral')}"
        evidence.append({"kind": "structure", "direction": str(item.get("bias", "neutral")).upper(), "detail": detail, "timeframe": tf, "strength": 0.7, "source": "binance_klines", "engine": "decision_engine_v3", "timestamp": snapshot.get("source_timestamps", {}).get(f"candles.{tf}"), "limitations": ["closed-OHLCV proxy"]})
    for item in deterministic.get("supporting_evidence", []) + deterministic.get("contradicting_evidence", []):
        evidence.append({**item, "source": "decision_engine_v3", "engine": "decision_engine_v3", "timestamp": snapshot.get("timestamp"), "limitations": ["deterministic proxy evidence"]})
    normalized = []
    for item in evidence[:max(1, min(max_evidence, 100))]:
        stable = _json({key: item.get(key) for key in ("kind", "direction", "detail", "timeframe", "source", "engine", "timestamp")})
        normalized.append({"id": f"ev_{hashlib.sha256(stable.encode()).hexdigest()[:16]}", **item})
    state = {"version": "verified-market-state-1", "symbol": snapshot.get("symbol"), "current_price": _number(snapshot.get("price")), "mark_price": _number(snapshot.get("mark_price")), "data_freshness": {"valid": freshness.get("valid", False), "fresh": freshness.get("fresh", False), "missing": freshness.get("missing", []), "stale": freshness.get("stale", [])}, "timeframes": {tf: {key: value for key, value in item.items() if key in {"bias", "structure", "bos", "choch", "last_close", "swing_high", "swing_low", "atr"}} for tf, item in deterministic.get("structure", {}).items()}, "futures_context": deterministic.get("futures_context", {}), "btc_context": deterministic.get("btc_context", {}), "risk_context": {key: deterministic.get(key) for key in ("entry", "stop_loss", "take_profits", "expected_rr", "setup_zone")}, "limitations": list(dict.fromkeys(deterministic.get("limitations", []) + ["Evidence is bounded and derived from the current v3 snapshot."])), "evidence": normalized}
    state["fingerprint"] = hashlib.sha256(_json({key: value for key, value in state.items() if key != "fingerprint"}).encode()).hexdigest()
    return state


class TradingBrainService:
    """One structured LLM call with a hard timeout and no network activity unless enabled."""
    def __init__(self, invoke: Callable[[str], Any] | None = None, environ: dict[str, str] | None = None):
        self.env, self._invoke = environ or os.environ, invoke
        self.enabled = self.env.get("TRADINGAGENTS_CHAT_LLM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
        self.provider = self.env.get("TRADINGAGENTS_CHAT_LLM_PROVIDER", "openai_compatible")
        self.model = self.env.get("TRADINGAGENTS_CHAT_LLM_MODEL", "")
        self.base_url = self.env.get("TRADINGAGENTS_CHAT_LLM_BASE_URL")
        self.timeout = max(1, int(self.env.get("TRADINGAGENTS_CHAT_LLM_TIMEOUT_SECONDS", "15")))
        self.max_output = max(128, int(self.env.get("TRADINGAGENTS_CHAT_LLM_MAX_OUTPUT", "1200")))

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.model) and (self._invoke is not None or bool(self.base_url) or self.provider in {"anthropic", "google", "bedrock", "azure"})

    def _call(self, prompt: str) -> tuple[dict[str, Any] | None, str | None]:
        if not self.ready:
            return None, "llm_not_configured"
        result: list[Any] = []
        def run() -> None:
            try:
                if self._invoke:
                    result.append(self._invoke(prompt))
                else:
                    llm = create_llm_client(self.provider, self.model, self.base_url, max_tokens=self.max_output).get_llm()
                    result.append(llm.invoke(prompt))
            except Exception as exc: result.append(exc)
        thread = threading.Thread(target=run, daemon=True); thread.start(); thread.join(self.timeout)
        if thread.is_alive(): return None, "llm_timeout"
        if not result or isinstance(result[0], Exception): return None, "llm_error"
        content = getattr(result[0], "content", result[0])
        if not isinstance(content, str): return None, "llm_malformed"
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError: return None, "llm_malformed"
        return parsed if isinstance(parsed, dict) else None, None

    def propose(self, state: dict[str, Any], correction_issues: list[str] | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        contract = {"directional_bias": "LONG|SHORT|NEUTRAL", "entry_status": "READY|WAIT|NO_TRADE|UNAVAILABLE", "confidence": "0..1", "entry_zone": {"low": "number|null", "high": "number|null"}, "stop_loss": "number|null", "take_profits": ["number"], "invalidation": "number|null", "supporting_evidence": [{"evidence_ids": ["ev_id"], "summary": "concise"}], "contradicting_evidence": [{"evidence_ids": ["ev_id"], "summary": "concise"}], "historical_context_used": ["ev_id"], "decision_summary": "concise evidence-based summary"}
        prompt = "You are TradingAgent's reasoning brain. Use only VERIFIED MARKET STATE evidence IDs. Never invent facts, liquidation/history claims, or levels. Return JSON only matching REQUIRED OUTPUT. Do not expose reasoning steps.\nREQUIRED OUTPUT=" + _json(contract) + "\nVERIFIED MARKET STATE=" + _json(state)
        if correction_issues: prompt += "\nCorrect these validator issues using only supplied evidence: " + _json(correction_issues)
        proposal, error = self._call(prompt)
        return proposal, {"enabled": self.enabled, "ready": self.ready, "provider": self.provider, "model": self.model, "status": "ok" if proposal else error, "correction": bool(correction_issues)}


class DecisionValidator:
    def validate(self, proposal: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        issues: list[str] = []; ids = {item["id"] for item in state["evidence"]}; freshness = state["data_freshness"]
        if not freshness.get("valid") or not freshness.get("fresh"): issues.append("stale_or_unavailable_market_data")
        bias, status = proposal.get("directional_bias"), proposal.get("entry_status")
        if bias not in {"LONG", "SHORT", "NEUTRAL"}: issues.append("invalid_direction")
        if status not in {"READY", "WAIT", "NO_TRADE", "UNAVAILABLE"}: issues.append("invalid_entry_status")
        confidence = _number(proposal.get("confidence"))
        if confidence is None or not 0 <= confidence <= 1: issues.append("invalid_confidence")
        referenced: list[str] = []
        for group in (proposal.get("supporting_evidence", []), proposal.get("contradicting_evidence", [])):
            if not isinstance(group, list): issues.append("invalid_evidence_format"); continue
            for claim in group:
                if not isinstance(claim, dict): issues.append("invalid_evidence_claim"); continue
                referenced.extend(claim.get("evidence_ids", []) if isinstance(claim.get("evidence_ids", []), list) else [])
                text = str(claim.get("summary", "")).lower()
                if any(word in text for word in ("liquidation", "historical")) and not claim.get("evidence_ids"): issues.append("unsupported_history_or_liquidation_claim")
        if any(ref not in ids for ref in referenced): issues.append("unknown_evidence_citation")
        if bias in {"LONG", "SHORT"} and not referenced: issues.append("missing_directional_citations")
        price = state.get("current_price"); entry = proposal.get("entry_zone") or {}; low, high = _number(entry.get("low")) if isinstance(entry, dict) else None, _number(entry.get("high")) if isinstance(entry, dict) else None
        stop, targets = _number(proposal.get("stop_loss")), proposal.get("take_profits", [])
        if status == "READY":
            if price is None or low is None or high is None or low > high or not (price * .85 <= (low + high) / 2 <= price * 1.15): issues.append("entry_far_from_current_price")
            if stop is None or not isinstance(targets, list) or not targets: issues.append("incomplete_risk_plan")
            elif bias == "LONG" and (stop >= low or any(_number(t) is None or _number(t) <= high for t in targets)): issues.append("invalid_long_risk_plan")
            elif bias == "SHORT" and (stop <= high or any(_number(t) is None or _number(t) >= low for t in targets)): issues.append("invalid_short_risk_plan")
        if "liquidation" in str(proposal).lower() and not any("liquid" in str(item).lower() for item in state["evidence"]): issues.append("unsupported_liquidation_claim")
        return {"valid": not issues, "issues": sorted(set(issues)), "safe_metadata": {"citation_count": len(referenced), "fresh": bool(freshness.get("fresh"))}}


def _install_store_methods() -> None:
    tables = {"analysis_records": "id TEXT PRIMARY KEY, conversation_id TEXT, symbol TEXT NOT NULL, fingerprint TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL", "analysis_evidence": "id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL", "llm_reasoning_records": "id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL", "validator_records": "id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL", "analysis_cache_entries": "fingerprint TEXT PRIMARY KEY, analysis_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT", "learning_records": "id TEXT PRIMARY KEY, trade_id TEXT, analysis_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL"}
    def initialize_hybrid(self: Store) -> None:
        with self._lock, self._connection() as conn:
            for name, columns in tables.items(): conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")
            if self.is_sqlite:
                try: conn.execute("ALTER TABLE analysis_cache_entries ADD COLUMN expires_at TEXT")
                except Exception: pass
            else: conn.execute("ALTER TABLE analysis_cache_entries ADD COLUMN IF NOT EXISTS expires_at TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_analysis_conversation ON analysis_records(conversation_id, created_at)")
    def analysis(self: Store, analysis_id: str) -> dict[str, Any] | None:
        with self._connection() as conn: row = conn.execute("SELECT payload_json FROM analysis_records WHERE id=?", (analysis_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def latest_analysis(self: Store, conversation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn: row = conn.execute("SELECT payload_json FROM analysis_records WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1", (conversation_id,)).fetchone()
        return json.loads(row[0]) if row else None
    Store.initialize_hybrid, Store.analysis, Store.latest_analysis = initialize_hybrid, analysis, latest_analysis
_install_store_methods()


class HybridAnalysisService:
    def __init__(self, store: Store, brain: TradingBrainService | None = None, cache_ttl_seconds: int | None = None):
        self.store, self.brain, self.validator = store, brain or TradingBrainService(), DecisionValidator(); self.store.initialize_hybrid()
        self.cache_ttl_seconds = max(1, cache_ttl_seconds if cache_ttl_seconds is not None else int(os.getenv("TRADINGAGENTS_ANALYSIS_CACHE_TTL_SECONDS", "300")))
        self.metrics = {"llm_calls": 0, "correction_calls": 0, "analysis_cache_hits": 0, "analysis_cache_misses": 0, "market_requests": 0, "analysis_reuses": 0}
    @staticmethod
    def _valid_until(value: str | None) -> bool:
        try: return bool(value and datetime.fromisoformat(value) > datetime.now(timezone.utc))
        except (TypeError, ValueError): return False
    def latest_reusable(self, conversation_id: str, symbol: str | None = None) -> dict[str, Any] | None:
        result = self.store.latest_analysis(conversation_id)
        if result and (not symbol or result.get("symbol") == symbol) and self._valid_until(result.get("expires_at")):
            self.metrics["analysis_reuses"] += 1
            return {**result, "cache": "reused"}
        return None
    def analyze(self, snapshot: dict[str, Any], conversation_id: str | None = None) -> dict[str, Any]:
        self.metrics["market_requests"] += 1; deterministic = decide(snapshot); state = verified_market_state(snapshot, deterministic)
        with self.store._connection() as conn: cached = conn.execute("SELECT analysis_id, expires_at FROM analysis_cache_entries WHERE fingerprint=?", (state["fingerprint"],)).fetchone()
        if cached and self._valid_until(cached[1]):
            result = self.store.analysis(cached[0])
            if result: self.metrics["analysis_cache_hits"] += 1; return {**result, "cache": "hit"}
        self.metrics["analysis_cache_misses"] += 1; proposal, llm_meta = self.brain.propose(state); self.metrics["llm_calls"] += int(proposal is not None)
        validation = self.validator.validate(proposal, state) if proposal else {"valid": False, "issues": [llm_meta["status"]], "safe_metadata": {}}
        if proposal and not validation["valid"]:
            corrected, correction_meta = self.brain.propose(state, validation["issues"]); self.metrics["correction_calls"] += int(corrected is not None); self.metrics["llm_calls"] += int(corrected is not None)
            if corrected:
                corrected_validation = self.validator.validate(corrected, state)
                if corrected_validation["valid"]: proposal, validation, llm_meta = corrected, corrected_validation, correction_meta
        final = self._final_decision(deterministic, proposal if validation["valid"] else None)
        analysis_id = uuid.uuid4().hex; created_at = _now(); expires_at = (datetime.now(timezone.utc) + timedelta(seconds=self.cache_ttl_seconds)).isoformat()
        record = {"id": analysis_id, "conversation_id": conversation_id, "symbol": state["symbol"], "fingerprint": state["fingerprint"], "verified_state": state, "decision": final, "synthesis_mode": "llm_validated" if proposal and validation["valid"] else "deterministic_fallback", "cache": "miss", "llm_result": llm_meta, "validation_result": validation, "created_at": created_at, "expires_at": expires_at}
        with self.store._lock, self.store._connection() as conn:
            conn.execute("INSERT INTO analysis_records VALUES (?,?,?,?,?,?)", (analysis_id, conversation_id, state["symbol"], state["fingerprint"], _json(record), record["created_at"]))
            for item in state["evidence"]: conn.execute("INSERT INTO analysis_evidence VALUES (?,?,?,?)", (f"{analysis_id}:{item['id']}", analysis_id, _json(item), record["created_at"]))
            conn.execute("INSERT INTO llm_reasoning_records VALUES (?,?,?,?)", (uuid.uuid4().hex, analysis_id, _json(llm_meta), record["created_at"]))
            conn.execute("INSERT INTO validator_records VALUES (?,?,?,?)", (uuid.uuid4().hex, analysis_id, _json(validation), record["created_at"]))
            conn.execute("INSERT INTO analysis_cache_entries (fingerprint,analysis_id,created_at,expires_at) VALUES (?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET analysis_id=excluded.analysis_id,created_at=excluded.created_at,expires_at=excluded.expires_at", (state["fingerprint"], analysis_id, record["created_at"], expires_at))
        return record
    @staticmethod
    def _final_decision(deterministic: dict[str, Any], proposal: dict[str, Any] | None) -> dict[str, Any]:
        if not proposal: return {**deterministic, "synthesis_mode": "deterministic_fallback"}
        bias, status = proposal["directional_bias"], proposal["entry_status"]
        state = "NO_TRADE" if bias == "NEUTRAL" or status == "NO_TRADE" else f"{bias}_{'READY' if status == 'READY' else 'WAIT'}"
        return {**deterministic, **proposal, "direction": bias if bias != "NEUTRAL" else "NO_TRADE", "decision_state": state, "tradeable": state.endswith("_READY"), "synthesis_mode": "llm_validated"}
    def record_learning(self, trade_id: str, outcome: dict[str, Any]) -> None:
        trade = self.store.trade(trade_id) or {}; analysis_id = trade.get("analysis_id"); payload = {"trade_id": trade_id, "analysis_id": analysis_id, "outcome": outcome.get("status"), "attribution": "NORMAL_LOSS" if outcome.get("status") == "SL_HIT" else "UNKNOWN", "action": "observe_only_no_auto_promotion"}
        with self.store._lock, self.store._connection() as conn: conn.execute("INSERT INTO learning_records VALUES (?,?,?,?,?)", (uuid.uuid4().hex, trade_id, analysis_id, _json(payload), _now()))
