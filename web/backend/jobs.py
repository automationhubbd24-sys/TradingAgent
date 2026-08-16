from __future__ import annotations

import copy
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingagents.dataflows.binance import normalize_binance_symbol
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

from .schemas import AnalyzeRequest, RunDetail, RunStatus, RunSummary

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_RESULTS_ROOT = PROJECT_ROOT / ".tradingagents" / "web_runs"
_ALLOWED_ANALYSTS = {"market", "social", "news", "fundamentals"}

_jobs: dict[str, "RunRecord"] = {}
_jobs_lock = threading.Lock()
_graph_lock = threading.Lock()


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


@dataclass
class RunRecord:
    run_id: str
    request: AnalyzeRequest
    status: RunStatus = "queued"
    created_at: str = field(default_factory=_now)
    completed_at: str | None = None
    error: str | None = None
    decision: str | None = None
    report: str | None = None
    report_path: str | None = None
    sections: dict[str, str] = field(default_factory=dict)

    def summary(self) -> RunSummary:
        return RunSummary(
            run_id=self.run_id,
            status=self.status,
            ticker=self.request.ticker,
            analysis_date=self.request.analysis_date,
            created_at=self.created_at,
            completed_at=self.completed_at,
            error=self.error,
        )

    def detail(self) -> RunDetail:
        return RunDetail(
            **self.summary().model_dump(),
            decision=self.decision,
            report=self.report,
            report_path=self.report_path,
            sections=self.sections,
        )


def list_runs() -> list[RunSummary]:
    with _jobs_lock:
        records = list(_jobs.values())
    return [record.summary() for record in sorted(records, key=lambda item: item.created_at, reverse=True)]


def get_run(run_id: str) -> RunDetail | None:
    with _jobs_lock:
        record = _jobs.get(run_id)
    return record.detail() if record else None


def create_run(request: AnalyzeRequest) -> str:
    request = _normalize_request(request)
    run_id = uuid.uuid4().hex
    record = RunRecord(run_id=run_id, request=request)
    with _jobs_lock:
        _jobs[run_id] = record
    thread = threading.Thread(target=_run_analysis, args=(run_id,), daemon=True)
    thread.start()
    return run_id


def _resolve_analysis_date(request: AnalyzeRequest) -> str:
    date_value = request.analysis_date.strip()
    if request.use_latest_date or not date_value:
        return datetime.now().strftime("%Y-%m-%d")
    return date_value


def _normalize_request(request: AnalyzeRequest) -> AnalyzeRequest:
    data = request.model_dump()
    data["ticker"] = request.ticker.strip().upper()
    data["analysis_date"] = _resolve_analysis_date(request)
    data["market_data_vendor"] = request.market_data_vendor.strip().lower()
    data["binance_interval"] = request.binance_interval.strip() or "1d"
    data["binance_quote_asset"] = request.binance_quote_asset.strip().upper() or "USDT"
    if data["market_data_vendor"] in {"binance_spot", "binance_futures"}:
        data["asset_type"] = "crypto"
        data["ticker"] = normalize_binance_symbol(data["ticker"], data["binance_quote_asset"])
    data["llm_provider"] = request.llm_provider.strip().lower()
    data["backend_url"] = request.backend_url.strip() if request.backend_url else None
    data["quick_think_llm"] = request.quick_think_llm.strip()
    data["deep_think_llm"] = request.deep_think_llm.strip()
    data["output_language"] = request.output_language.strip() or "English"
    data["analysts"] = [
        analyst.strip().lower()
        for analyst in request.analysts
        if analyst.strip().lower() in _ALLOWED_ANALYSTS
    ]
    if data["asset_type"] == "crypto":
        data["analysts"] = [analyst for analyst in data["analysts"] if analyst != "fundamentals"]
    if not data["analysts"]:
        data["analysts"] = ["market", "social", "news"] if data["asset_type"] == "crypto" else ["market", "social", "news", "fundamentals"]
    return AnalyzeRequest(**data)


def _set_status(run_id: str, **updates: Any) -> RunRecord:
    with _jobs_lock:
        record = _jobs[run_id]
        for key, value in updates.items():
            setattr(record, key, value)
        return record


def _run_analysis(run_id: str) -> None:
    record = _set_status(run_id, status="running")
    try:
        with _graph_lock:
            _execute_run(record)
    except Exception as exc:  # pragma: no cover - surfaced via API
        _set_status(run_id, status="failed", error=str(exc), completed_at=_now())


def _execute_run(record: RunRecord) -> None:
    request = record.request
    _ensure_runtime_dirs()
    previous_key = os.environ.get("OPENAI_COMPATIBLE_API_KEY")
    should_set_key = request.llm_provider == "openai_compatible" and request.api_key
    try:
        if should_set_key:
            os.environ["OPENAI_COMPATIBLE_API_KEY"] = request.api_key or ""

        config = _build_config(request)
        graph = TradingAgentsGraph(
            selected_analysts=tuple(request.analysts),
            debug=False,
            config=config,
        )
        final_state, decision = graph.propagate(
            request.ticker,
            request.analysis_date,
            asset_type=request.asset_type,
        )
        save_dir = WEB_RESULTS_ROOT / record.run_id / "reports"
        report_path = graph.save_reports(final_state, request.ticker, save_path=save_dir)
        report = report_path.read_text(encoding="utf-8") if report_path.exists() else None
        sections = _extract_sections(final_state)
        _set_status(
            record.run_id,
            status="completed",
            completed_at=_now(),
            decision=str(decision),
            report=report,
            report_path=str(report_path),
            sections=sections,
        )
    finally:
        if should_set_key:
            if previous_key is None:
                os.environ.pop("OPENAI_COMPATIBLE_API_KEY", None)
            else:
                os.environ["OPENAI_COMPATIBLE_API_KEY"] = previous_key


def _ensure_runtime_dirs() -> None:
    local_root = PROJECT_ROOT / ".tradingagents"
    for path in (
        local_root / "logs",
        local_root / "cache",
        local_root / "cache" / "checkpoints",
        local_root / "memory",
        WEB_RESULTS_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _build_config(request: AnalyzeRequest) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    local_root = PROJECT_ROOT / ".tradingagents"
    if request.market_data_vendor in {"binance_spot", "binance_futures"}:
        config["data_vendors"]["core_stock_apis"] = request.market_data_vendor
        config["data_vendors"]["technical_indicators"] = request.market_data_vendor

    config.update(
        {
            "results_dir": str(local_root / "logs"),
            "data_cache_dir": str(local_root / "cache"),
            "memory_log_path": str(local_root / "memory" / "trading_memory.md"),
            "llm_provider": request.llm_provider,
            "backend_url": request.backend_url,
            "quick_think_llm": request.quick_think_llm,
            "deep_think_llm": request.deep_think_llm,
            "output_language": request.output_language,
            "max_debate_rounds": request.max_debate_rounds,
            "max_risk_discuss_rounds": request.max_risk_discuss_rounds,
            "checkpoint_enabled": request.checkpoint_enabled,
            "binance_interval": request.binance_interval,
            "binance_quote_asset": request.binance_quote_asset,
        }
    )
    return config


def _extract_sections(final_state: dict[str, Any]) -> dict[str, str]:
    sections: dict[str, str] = {}
    direct_keys = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "trader_investment_plan",
        "final_trade_decision",
    ]
    for key in direct_keys:
        value = final_state.get(key)
        if value:
            sections[key] = str(value)

    debate = final_state.get("investment_debate_state") or {}
    if debate.get("judge_decision"):
        sections["research_manager"] = str(debate["judge_decision"])

    risk = final_state.get("risk_debate_state") or {}
    if risk.get("judge_decision"):
        sections["portfolio_decision"] = str(risk["judge_decision"])
    return sections
