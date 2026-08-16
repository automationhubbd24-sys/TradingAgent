from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .jobs import create_run, get_run, list_runs
from .schemas import AnalyzeRequest, CreateRunResponse, RunDetail, RunSummary

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"

app = FastAPI(title="TradingAgents Web API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/options")
def options() -> dict:
    return {
        "analysts": [
            {"value": "market", "label": "Market Analyst"},
            {"value": "social", "label": "Sentiment Analyst"},
            {"value": "news", "label": "News Analyst"},
            {"value": "fundamentals", "label": "Fundamentals Analyst"},
        ],
        "asset_types": ["stock", "crypto"],
        "market_data_vendors": [
            {"value": "yfinance", "label": "Yahoo Finance"},
            {"value": "binance_spot", "label": "Binance Spot"},
            {"value": "binance_futures", "label": "Binance USD-M Futures"},
        ],
        "binance_intervals": ["1m", "5m", "15m", "1h", "4h", "1d"],
        "binance_examples": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "1000PEPEUSDT", "BTC/USDT:USDT"],
        "defaults": {
            "ticker": "NVDA",
            "analysis_date": "latest",
            "llm_provider": "openai_compatible",
            "backend_url": "https://gemini.salesmanchatbot.online/v1",
            "quick_think_llm": "gemini-3.6-flash",
            "deep_think_llm": "gemini-3.6-flash",
            "output_language": "English",
            "market_data_vendor": "yfinance",
            "binance_interval": "1d",
            "binance_quote_asset": "USDT",
        },
    }


@app.post("/api/runs", response_model=CreateRunResponse)
def start_run(request: AnalyzeRequest) -> CreateRunResponse:
    return CreateRunResponse(run_id=create_run(request))


@app.get("/api/runs", response_model=list[RunSummary])
def runs() -> list[RunSummary]:
    return list_runs()


@app.get("/api/runs/{run_id}", response_model=RunDetail)
def run_detail(run_id: str) -> RunDetail:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str) -> dict[str, str | None]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "completed":
        raise HTTPException(status_code=409, detail="Run is not completed yet")
    return {"run_id": run_id, "report": run.report, "report_path": run.report_path}


if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str) -> FileResponse:
        target = FRONTEND_DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(FRONTEND_DIST / "index.html")
