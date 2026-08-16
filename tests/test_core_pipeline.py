from pathlib import Path

from web.backend.chat import parse_intent, stream_chat
from web.backend.foundation import Store, database_url
from web.backend.synthesis import configured_synthesizer


def test_intent_routing_is_explicit_and_general_needs_no_market():
    expected = {
        "Should I buy or short BTCUSDT?": "buy_or_short",
        "Build a long BTCUSDT setup": "long_setup",
        "Build a short BTCUSDT setup": "short_setup",
        "Why is BTCUSDT weak?": "why",
        "Explain BTCUSDT structure": "explain",
        "What is the risk on BTCUSDT?": "risk_question",
        "Compare BTCUSDT and ETHUSDT": "compare_assets",
        "Show history": "history",
        "Show performance": "performance",
        "hello": "general",
    }
    for message, intent in expected.items():
        assert parse_intent(message)[0] == intent
    assert parse_intent("continue", "BTCUSDT")[0] == "follow_up"


def test_general_chat_does_not_call_market_and_graph_is_disabled(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'chat.sqlite'}")
    conversation = store.create_conversation()
    class Market:
        def with_btc_context(self, symbol): raise AssertionError("ordinary chat must not fetch market")
    events = "".join(stream_chat(store, Market(), conversation["id"], "hello"))
    assert '"intent":"general"' in events
    assert "fetching_market_data" not in events
    monkeypatch.delenv("TRADINGAGENTS_CHAT_GRAPH_SYNTHESIS", raising=False)
    assert configured_synthesizer().summarize({}, {}) is None


def test_postgres_url_and_migration_are_network_free():
    assert database_url("postgresql://user:pass@db:5432/app") == "postgresql+psycopg://user:pass@db:5432/app"
    migration = Path("alembic/versions/0001_core_pipeline.py").read_text(encoding="utf-8")
    assert "conversations" in migration and "trade_decisions" in migration
