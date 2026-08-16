from __future__ import annotations

from datetime import datetime, timedelta, timezone

from web.backend.chat import stream_chat
from web.backend.foundation import Store
from web.backend.hybrid import DecisionValidator, HybridAnalysisService, TradingBrainService, verified_market_state


def snapshot() -> dict:
    return {"symbol": "BTCUSDT", "price": 100.0, "mark_price": 100.0, "timestamp": 1, "source_timestamps": {}, "quality": {"valid": True, "fresh": True, "missing": [], "stale": []}}


def deterministic() -> dict:
    return {"structure": {"1h": {"structure": "HH_HL", "bias": "bullish"}}, "supporting_evidence": [], "contradicting_evidence": [], "limitations": []}


def test_verified_state_evidence_is_stable_and_bounded():
    state = verified_market_state(snapshot(), deterministic(), max_evidence=1)
    again = verified_market_state(snapshot(), deterministic(), max_evidence=1)
    assert state["fingerprint"] == again["fingerprint"]
    assert len(state["evidence"]) == 1


def test_llm_disabled_falls_back(tmp_path, monkeypatch):
    service = HybridAnalysisService(Store(f"sqlite:///{tmp_path / 'db.sqlite'}"), TradingBrainService(environ={}))
    monkeypatch.setattr("web.backend.hybrid.decide", lambda _: {**deterministic(), "decision_state": "NO_TRADE", "direction": "NO_TRADE"})
    result = service.analyze(snapshot())
    assert result["synthesis_mode"] == "deterministic_fallback"


def test_valid_llm_proposal_and_liquidation_rejection():
    state = verified_market_state(snapshot(), deterministic())
    evidence_id = state["evidence"][0]["id"]
    proposal = {"directional_bias": "LONG", "entry_status": "WAIT", "confidence": .6, "supporting_evidence": [{"evidence_ids": [evidence_id], "summary": "structure supports"}], "contradicting_evidence": []}
    assert DecisionValidator().validate(proposal, state)["valid"]
    proposal["supporting_evidence"] = [{"evidence_ids": [evidence_id], "summary": "liquidation cascade"}]
    assert "unsupported_liquidation_claim" in DecisionValidator().validate(proposal, state)["issues"]


def test_cache_expiry_and_followup_reuse_without_market_snapshot(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}")
    conversation = store.create_conversation(active_symbol="BTCUSDT")
    service = HybridAnalysisService(store, TradingBrainService(environ={}), cache_ttl_seconds=1)
    monkeypatch.setattr("web.backend.hybrid.decide", lambda _: {**deterministic(), "decision_state": "NO_TRADE", "direction": "NO_TRADE"})
    record = service.analyze(snapshot(), conversation["id"])
    assert service.latest_reusable(conversation["id"], "BTCUSDT")
    with store._connection() as conn:
        conn.execute("UPDATE analysis_cache_entries SET expires_at=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    assert service.analyze(snapshot(), conversation["id"])["cache"] == "miss"
    monkeypatch.setattr(service, "analyze", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("follow-up must reuse cached analysis")))
    events = list(stream_chat(store, object(), conversation["id"], "why BTCUSDT?", hybrid_service=service))
    assert any('"cache":"reused"' in event for event in events)


def test_shared_service_metrics_and_analysis_retrieval(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'db.sqlite'}")
    service = HybridAnalysisService(store, TradingBrainService(environ={}))
    monkeypatch.setattr("web.backend.hybrid.decide", lambda _: {**deterministic(), "decision_state": "NO_TRADE", "direction": "NO_TRADE"})
    conversation = store.create_conversation(active_symbol="BTCUSDT")
    record = service.analyze(snapshot(), conversation["id"])
    assert store.analysis(record["id"])["id"] == record["id"]
    assert service.metrics["market_requests"] == 1
