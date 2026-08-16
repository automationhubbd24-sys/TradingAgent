from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from .chat import stream_chat
from .foundation import BinanceMarketService, BinanceWebSocketManager, OutcomeService, Store, TERMINAL_OUTCOMES
from .jobs import create_run, get_run, list_runs
from .schemas import AnalyzeRequest, CreateRunResponse, RunDetail, RunSummary

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
app = FastAPI(title="TradingAgents Web API", version="0.2.0")
store, market, websocket_manager = Store(), BinanceMarketService(), BinanceWebSocketManager()

app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


class ConversationCreate(BaseModel):
    title: str = "New conversation"
    active_symbol: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    active_symbol: str | None = None


class ChatRequest(BaseModel):
    conversation_id: str
    message: str = Field(min_length=1, max_length=4000)


class TradeEvaluationRequest(BaseModel):
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    observed_at: datetime | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_ohlc(self) -> "TradeEvaluationRequest":
        if self.low > self.high or not self.low <= self.close <= self.high:
            raise ValueError("OHLC fields must satisfy low <= close <= high")
        return self


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        store.initialize()
        database = "ok"
    except Exception as exc:  # pragma: no cover
        database = f"error: {exc}"
    return {"status": "ok" if database == "ok" else "degraded", "database": database, "binance_rest": {"configured": True}, "binance_websocket": websocket_manager.status(), "workers": "not_configured", "llm": "not_required_for_chat", "execution": "paper_only"}


@app.get("/api/options")
def options() -> dict:
    return {"analysts": [{"value": value, "label": label} for value, label in [("market", "Market Analyst"), ("social", "Sentiment Analyst"), ("news", "News Analyst"), ("fundamentals", "Fundamentals Analyst")]], "asset_types": ["stock", "crypto"], "market_data_vendors": [{"value": "yfinance", "label": "Yahoo Finance"}, {"value": "binance_spot", "label": "Binance Spot"}, {"value": "binance_futures", "label": "Binance USD-M Futures"}], "binance_intervals": ["1m", "5m", "15m", "1h", "4h", "1d"], "binance_examples": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "1000PEPEUSDT", "BTC/USDT:USDT"], "defaults": {"ticker": "NVDA", "analysis_date": "latest", "llm_provider": "openai_compatible", "backend_url": "https://gemini.salesmanchatbot.online/v1", "quick_think_llm": "gemini-3.6-flash", "deep_think_llm": "gemini-3.6-flash", "output_language": "English", "market_data_vendor": "yfinance", "binance_interval": "1d", "binance_quote_asset": "USDT"}}


@app.post("/api/runs", response_model=CreateRunResponse)
def start_run(request: AnalyzeRequest) -> CreateRunResponse: return CreateRunResponse(run_id=create_run(request))
@app.get("/api/runs", response_model=list[RunSummary])
def runs() -> list[RunSummary]: return list_runs()
@app.get("/api/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str) -> RunDetail:
    run = get_run(run_id)
    if run is None: raise HTTPException(status_code=404, detail="Run not found")
    return run
@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str) -> dict[str, str | None]:
    run = get_run(run_id)
    if run is None: raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "completed": raise HTTPException(status_code=409, detail="Run is not completed yet")
    return {"run_id": run_id, "report": run.report, "report_path": run.report_path}


@app.get("/api/conversations")
def conversations() -> list[dict[str, Any]]: return store.list_conversations()
@app.post("/api/conversations", status_code=201)
def create_conversation(request: ConversationCreate) -> dict[str, Any]: return store.create_conversation(request.title, request.active_symbol)
@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: str) -> dict[str, Any]:
    conversation = store.conversation(conversation_id)
    if not conversation: raise HTTPException(404, "Conversation not found")
    return {**conversation, "messages": store.messages(conversation_id)}
@app.patch("/api/conversations/{conversation_id}")
def patch_conversation(conversation_id: str, request: ConversationPatch) -> dict[str, Any]:
    conversation = store.update_conversation(conversation_id, **request.model_dump(exclude_unset=True))
    if not conversation: raise HTTPException(404, "Conversation not found")
    return conversation
@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: str) -> None:
    if not store.delete_conversation(conversation_id): raise HTTPException(404, "Conversation not found")
@app.get("/api/conversations/{conversation_id}/messages")
def messages(conversation_id: str) -> list[dict[str, Any]]:
    if not store.conversation(conversation_id): raise HTTPException(404, "Conversation not found")
    return store.messages(conversation_id)
@app.post("/api/chat")
def chat(request: ChatRequest) -> StreamingResponse:
    if not store.conversation(request.conversation_id): raise HTTPException(404, "Conversation not found")
    return StreamingResponse(stream_chat(store, market, request.conversation_id, request.message), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/market/{symbol}")
def market_snapshot(symbol: str) -> dict[str, Any]:
    try: return market.with_btc_context(symbol)
    except Exception as exc: raise HTTPException(503, f"Market data unavailable: {exc}") from exc
@app.get("/api/trades")
def trades() -> list[dict[str, Any]]: return store.trades()
@app.get("/api/trades/{trade_id}")
def trade_detail(trade_id: str) -> dict[str, Any]:
    trade = store.trade(trade_id)
    if not trade: raise HTTPException(404, "Trade not found")
    return trade
@app.post("/api/trades/{trade_id}/evaluate")
def evaluate_trade(trade_id: str, request: TradeEvaluationRequest) -> dict[str, Any]:
    if not store.trade(trade_id): raise HTTPException(404, "Trade not found")
    observation = request.model_dump(mode="json", exclude_none=True)
    store.save_trade_snapshot(trade_id, {**observation, "source": "manual_ohlc"})
    try:
        outcome, created = OutcomeService(store).evaluate(trade_id, source="manual_ohlc", **observation)
    except KeyError as exc:  # pragma: no cover - guarded above
        raise HTTPException(404, str(exc)) from exc
    return {"outcome": outcome, "created": created, "execution": "paper_only"}
@app.get("/api/trades/{trade_id}/postmortem")
def trade_postmortem(trade_id: str) -> dict[str, Any]:
    result = store.postmortem(trade_id)
    if not result: raise HTTPException(404, "Post-mortem not found")
    return result
@app.get("/api/lessons")
def lessons() -> list[dict[str, Any]]: return store.list_learning("lessons")
@app.get("/api/patterns")
def patterns() -> list[dict[str, Any]]: return store.list_learning("patterns")
@app.get("/api/strategies")
def strategies() -> list[dict[str, Any]]: return store.list_learning("strategy_versions")
@app.get("/api/performance")
def performance() -> dict[str, Any]:
    decisions = store.trades()
    actionable = [decision for decision in decisions if decision["direction"] != "NO_TRADE"]
    statuses = [store.outcome(decision["id"])["status"] if store.outcome(decision["id"]) else "OPEN" for decision in actionable]
    return {"decisions": len(decisions), "paper_positions": len(actionable), "open": statuses.count("OPEN"), "unresolved": statuses.count("UNRESOLVED"), "terminal": sum(status in TERMINAL_OUTCOMES for status in statuses), "execution": "disabled", "strategy_promotion": "disabled"}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        target = FRONTEND_DIST / full_path
        return FileResponse(target) if full_path and target.is_file() else FileResponse(FRONTEND_DIST / "index.html")
