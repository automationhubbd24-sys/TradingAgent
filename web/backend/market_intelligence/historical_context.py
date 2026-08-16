"""Compact, closed-candle historical profiles for Binance USD-M futures snapshots."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from statistics import median
from threading import RLock
from typing import Any, Callable

from .contracts import candle_time, num
from .regime import classify
from .structure import assess

_MIN_ANALOGUES = 12
_ANALOGUE_HORIZON = 12
_LOOKBACK = 20


def _futures_snapshot(snapshot: dict[str, Any]) -> bool:
    source = str(snapshot.get("source", "")).lower()
    market_type = str(snapshot.get("market_type", "")).lower()
    return "binance_usdm" in source or market_type in {"futures", "usdm_futures"}


def _closed(candles: list[dict[str, Any]], as_of: int | None) -> list[dict[str, Any]]:
    result = []
    for candle in candles:
        close = num(candle.get("close"))
        timestamp = candle_time(candle)
        if close is not None and (as_of is None or (timestamp is not None and timestamp <= as_of)):
            result.append(candle)
    return sorted(result, key=lambda candle: candle_time(candle) or 0)


def _return_pct(first: float, last: float) -> float:
    return (last - first) / first * 100 if first else 0.0


def _price_map(candles: list[dict[str, Any]], price: float) -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles]
    low, high = min(float(candle["low"]) for candle in candles), max(float(candle["high"]) for candle in candles)
    ordered = sorted(closes)
    percentile = sum(value <= price for value in ordered) / len(ordered) * 100
    range_position = (price - low) / (high - low) if high > low else 0.5
    returns = [_return_pct(closes[index - 1], closes[index]) for index in range(1, len(closes))]
    volatility = (sum(value * value for value in returns) / len(returns)) ** 0.5 if returns else None
    buckets: dict[float, int] = {}
    bucket_size = max((high - low) / 24, abs(price) * 0.001, 0.00000001)
    for candle in candles:
        midpoint = (float(candle["high"]) + float(candle["low"])) / 2
        bucket = round(round(midpoint / bucket_size) * bucket_size, 8)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    levels = sorted(buckets.items(), key=lambda entry: (abs(entry[0] - price), -entry[1]))[:3]
    return {
        "price": round(price, 8),
        "range_low": round(low, 8),
        "range_high": round(high, 8),
        "range_position": round(range_position, 3),
        "close_percentile": round(percentile, 1),
        "realized_volatility_pct": round(volatility, 3) if volatility is not None else None,
        "nearby_levels": [
            {"price": level, "touches": touches, "distance_pct": round(_return_pct(price, level), 3)}
            for level, touches in levels
        ],
    }


def _analogue_signature(candles: list[dict[str, Any]], index: int) -> tuple[int, int]:
    window = candles[index - _LOOKBACK:index]
    start, end = float(window[0]["close"]), float(window[-1]["close"])
    change = _return_pct(start, end)
    ranges = [float(candle["high"]) - float(candle["low"]) for candle in window]
    average_range = sum(ranges) / len(ranges)
    return (1 if change >= 0 else -1, 1 if average_range / max(abs(end), 0.00000001) >= 0.01 else 0)


def _analogues(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < _LOOKBACK + _ANALOGUE_HORIZON + 1:
        return {"status": "INSUFFICIENT_HISTORY", "sample_size": 0, "minimum_sample_size": _MIN_ANALOGUES, "horizon_bars": _ANALOGUE_HORIZON}
    signature = _analogue_signature(candles, len(candles))
    outcomes = []
    # Candidate signatures use only candles before the candidate; outcomes are closed history after it.
    for index in range(_LOOKBACK, len(candles) - _ANALOGUE_HORIZON):
        if _analogue_signature(candles, index) == signature:
            outcomes.append(_return_pct(float(candles[index]["close"]), float(candles[index + _ANALOGUE_HORIZON]["close"])))
    sample_size = len(outcomes)
    base = {"sample_size": sample_size, "minimum_sample_size": _MIN_ANALOGUES, "horizon_bars": _ANALOGUE_HORIZON}
    if sample_size < _MIN_ANALOGUES:
        return {"status": "INSUFFICIENT_SAMPLE", **base}
    positive = sum(value > 0 for value in outcomes)
    return {
        "status": "AVAILABLE",
        **base,
        "win_rate_pct": round(positive / sample_size * 100, 1),
        "median_forward_return_pct": round(median(outcomes), 3),
        "outcome_range_pct": [round(min(outcomes), 3), round(max(outcomes), 3)],
    }


def build(snapshot: dict[str, Any], history: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Build a compact profile from closed, Binance USD-M futures OHLCV only."""
    if not _futures_snapshot(snapshot):
        return {"status": "UNAVAILABLE", "reason": "Historical profiles are limited to Binance USD-M futures snapshots."}
    timeframe = "4h"
    source = history or snapshot.get("candles", {})
    as_of = candle_time({"close_time": snapshot.get("timestamp")})
    candles = _closed(source.get(timeframe, []), as_of)
    price = num(snapshot.get("price"))
    if price is None or len(candles) < 2:
        return {"status": "PARTIAL", "basis": "FUTURES_CLOSED_OHLCV", "timeframe": timeframe, "sample_size": len(candles), "reason": "Closed historical candles or current price are unavailable."}
    states = {timeframe: assess(candles, timeframe)}
    return {
        "status": "READY",
        "basis": "FUTURES_CLOSED_OHLCV",
        "timeframe": timeframe,
        "sample_size": len(candles),
        "as_of": candle_time(candles[-1]),
        "market_regime": classify(states),
        "current_price_map": _price_map(candles, price),
        "analogues": _analogues(candles),
        "limitations": ["Closed OHLCV only; no historical order-book, funding, open-interest, or liquidation inference.", "Analogue results are descriptive and sample-size-gated, not predictions."],
    }


class HistoricalProfileBuilder:
    """Returns a partial profile immediately while a supplied full-history loader runs."""
    def __init__(self, max_workers: int = 1):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="historical-profile")
        self._lock = RLock()
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._profiles: dict[str, dict[str, Any]] = {}
        self._profile_keys: dict[str, tuple[Any, Any]] = {}

    def profile(self, snapshot: dict[str, Any], history_loader: Callable[[str], dict[str, list[dict[str, Any]]]] | None = None) -> dict[str, Any]:
        symbol = str(snapshot.get("symbol", ""))
        profile_key = (snapshot.get("timestamp"), snapshot.get("price"))
        partial = build(snapshot)
        if partial["status"] == "UNAVAILABLE" or history_loader is None:
            return partial
        with self._lock:
            completed = self._profiles.get(symbol)
            if completed and self._profile_keys.get(symbol) == profile_key:
                return completed
            future = self._futures.get(symbol)
            if future is None or self._profile_keys.get(symbol) != profile_key:
                future = self._executor.submit(self._build_loaded, symbol, snapshot, history_loader)
                self._futures[symbol] = future
                self._profile_keys[symbol] = profile_key
            if future.done():
                try:
                    completed = future.result()
                except Exception:
                    completed = None
                if completed:
                    self._profiles[symbol] = completed
                    return completed
        return {**partial, "status": "BUILDING", "fallback": "PARTIAL_CURRENT_HISTORY"}

    def _build_loaded(self, symbol: str, snapshot: dict[str, Any], loader: Callable[[str], dict[str, list[dict[str, Any]]]]) -> dict[str, Any]:
        profile = build(snapshot, loader(symbol))
        with self._lock:
            self._profiles[symbol] = profile
        return profile

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
