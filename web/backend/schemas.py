from typing import Literal

from pydantic import BaseModel, Field

RunStatus = Literal["queued", "running", "completed", "failed"]
MarketDataVendor = Literal["yfinance", "binance_spot", "binance_futures"]


class AnalyzeRequest(BaseModel):
    ticker: str = Field(default="NVDA", min_length=1)
    analysis_date: str = Field(default="", min_length=0)
    use_latest_date: bool = True
    asset_type: Literal["stock", "crypto"] = "stock"
    market_data_vendor: MarketDataVendor = "yfinance"
    binance_interval: str = "1d"
    binance_quote_asset: str = "USDT"
    llm_provider: str = Field(default="openai_compatible", min_length=1)
    backend_url: str | None = "https://gemini.salesmanchatbot.online/v1"
    quick_think_llm: str = Field(default="gemini-3.6-flash", min_length=1)
    deep_think_llm: str = Field(default="gemini-3.6-flash", min_length=1)
    api_key: str | None = None
    output_language: str = "English"
    analysts: list[str] = Field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"]
    )
    max_debate_rounds: int = Field(default=1, ge=1)
    max_risk_discuss_rounds: int = Field(default=1, ge=1)
    checkpoint_enabled: bool = False


class RunSummary(BaseModel):
    run_id: str
    status: RunStatus
    ticker: str
    analysis_date: str
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class RunDetail(RunSummary):
    decision: str | None = None
    report: str | None = None
    report_path: str | None = None
    sections: dict[str, str] = Field(default_factory=dict)


class CreateRunResponse(BaseModel):
    run_id: str
