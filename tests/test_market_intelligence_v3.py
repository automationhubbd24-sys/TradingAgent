from web.backend.market_intelligence.decision import decide
from web.backend.market_intelligence.fvg import detect as fvg
from web.backend.market_intelligence.structure import assess


def rows(values):
    return [{"open_time": i, "close_time": i + 1, "open": v - .4, "high": v + 1, "low": v - 1, "close": v, "volume": 10 + (i % 3)} for i, v in enumerate(values)]


def snap(direction="up"):
    values=list(range(100,130)) if direction == "up" else list(range(130,100,-1))
    candles={tf: rows(values) for tf in ("4h","1h","30m","15m","5m")}
    return {"symbol":"BTCUSDT","price":float(values[-1]),"candles":candles,"quality":{"valid":True,"fresh":True}}


def test_states_are_json_serializable_and_hierarchy_ready():
    decision=decide(snap())
    assert decision["decision_state"] == "LONG_READY"
    assert decision["tradeable"] and decision["scores"]["overall"] >= 55
    pivot=decision["structure"]["4h"]["pivots"]["highs"]
    assert isinstance(pivot, list)
    assert decision["futures_context"]["liquidations"]["status"] == "UNAVAILABLE"


def test_hierarchy_wait_and_no_trade_are_distinct():
    waiting=snap(); waiting["candles"]["5m"] = rows(list(range(130,100,-1)))
    assert decide(waiting)["decision_state"] == "LONG_WAIT"
    mixed=snap(); mixed["candles"]["1h"] = rows(list(range(130,100,-1)))
    assert decide(mixed)["decision_state"] == "NO_TRADE"


def test_closed_structure_displacement_and_fvg_detectors():
    state=assess(rows([100,101,102,103,104,105,106]), "5m")
    assert state["displacement"]["grade"] in {"NONE","WEAK","MODERATE","STRONG","EXTREME"}
    gaps=fvg([{ "open": 1,"high": 2,"low": .5,"close": 1,"volume": 1},{"open":1,"high":2,"low":1,"close":2,"volume":1},{"open":3,"high":4,"low":3,"close":3.5,"volume":1}])
    assert gaps and gaps[0]["direction"] == "LONG" and gaps[0]["basis"] == "OHLCV_PROXY"


def test_data_unavailable_does_not_become_no_trade():
    result=decide({"symbol":"BTCUSDT","price":None,"candles":{},"quality":{"valid":False,"fresh":False,"missing":["ticker"]}})
    assert result["decision_state"] == "DATA_UNAVAILABLE" and not result["tradeable"]
