from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta
from stockstats import wrap

from .config import get_config
from .errors import NoMarketDataError, VendorRateLimitError

_SPOT_BASE_URL = "https://api.binance.com"
_FUTURES_BASE_URL = "https://fapi.binance.com"
_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD", "BTC", "ETH", "BNB")
_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance.",
    "close_200_sma": "200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups.",
    "close_10_ema": "10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points.",
    "macd": "MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades.",
    "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions and momentum strength.",
    "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands.",
    "boll_ub": "Bollinger Upper Band: Typically 2 standard deviations above the middle line.",
    "boll_lb": "Bollinger Lower Band: Typically 2 standard deviations below the middle line.",
    "atr": "ATR: Averages true range to measure volatility.",
    "vwma": "VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data.",
    "mfi": "MFI: Money Flow Index measures buying and selling pressure using price and volume.",
}


def normalize_binance_symbol(raw: str, default_quote: str = "USDT") -> str:
    symbol = (raw or "").strip().upper()
    if not symbol:
        raise ValueError("Binance symbol cannot be empty")

    if ":" in symbol:
        symbol = symbol.split(":", 1)[0]
    symbol = symbol.replace("/", "").replace("-", "").replace("_", "").replace(" ", "")
    default_quote = default_quote.upper()

    if symbol.endswith(default_quote):
        return symbol
    if default_quote == "USDT" and symbol.endswith("USD"):
        return symbol[:-3] + "USDT"
    if any(symbol.endswith(quote) for quote in _QUOTES):
        return symbol
    return symbol + default_quote


def get_binance_spot_data(symbol: str, start_date: str, end_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    df = _fetch_klines(canonical, start_date, end_date, market="spot")
    return _format_ohlcv_report("Binance Spot", symbol, canonical, start_date, end_date, df)


def get_binance_futures_data(symbol: str, start_date: str, end_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    df = _fetch_klines(canonical, start_date, end_date, market="futures")
    futures_context = get_binance_futures_context(symbol, end_date)
    report = _format_ohlcv_report("Binance USD-M Futures", symbol, canonical, start_date, end_date, df)
    return report + "\n\n" + futures_context


def get_binance_spot_indicators_window(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    return _get_binance_indicators_window(symbol, indicator, curr_date, look_back_days, market="spot")


def get_binance_futures_indicators_window(symbol: str, indicator: str, curr_date: str, look_back_days: int) -> str:
    return _get_binance_indicators_window(symbol, indicator, curr_date, look_back_days, market="futures")


def load_binance_spot_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    start_date = (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(years=5)).strftime("%Y-%m-%d")
    return _fetch_klines(normalize_binance_symbol(symbol, _default_quote()), start_date, curr_date, market="spot")


def load_binance_futures_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    start_date = (datetime.strptime(curr_date, "%Y-%m-%d") - relativedelta(years=5)).strftime("%Y-%m-%d")
    return _fetch_klines(normalize_binance_symbol(symbol, _default_quote()), start_date, curr_date, market="futures")


def get_binance_futures_funding_rate(symbol: str, curr_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    base_url = _futures_base_url()
    end_ms = _to_ms(curr_date, end_of_day=True)
    start_ms = _to_ms((datetime.strptime(curr_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d"))
    try:
        data = _request_json(
            f"{base_url}/fapi/v1/fundingRate",
            {"symbol": canonical, "startTime": start_ms, "endTime": end_ms, "limit": 20},
        )
    except Exception as exc:
        return f"Funding rate unavailable for {canonical}: {exc}"
    if not data:
        return f"Funding rate unavailable for {canonical}: no recent rows"
    latest = data[-1]
    rate = float(latest.get("fundingRate", 0))
    ts = datetime.fromtimestamp(int(latest.get("fundingTime", 0)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Latest Binance futures funding rate for {canonical}: {rate:.6%} at {ts}."


def get_binance_futures_open_interest(symbol: str, curr_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    try:
        data = _request_json(f"{_futures_base_url()}/fapi/v1/openInterest", {"symbol": canonical})
    except Exception as exc:
        return f"Open interest unavailable for {canonical}: {exc}"
    value = data.get("openInterest")
    time_ms = data.get("time")
    ts = "N/A"
    if time_ms:
        ts = datetime.fromtimestamp(int(time_ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Current Binance futures open interest for {canonical}: {value} contracts at {ts}."


def get_binance_futures_long_short_ratio(symbol: str, curr_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    try:
        data = _request_json(
            f"{_futures_base_url()}/futures/data/globalLongShortAccountRatio",
            {"symbol": canonical, "period": "1d", "limit": 7, "endTime": _to_ms(curr_date, end_of_day=True)},
        )
    except Exception as exc:
        return f"Long/short ratio unavailable for {canonical}: {exc}"
    if not data:
        return f"Long/short ratio unavailable for {canonical}: no recent rows"
    latest = data[-1]
    return (
        f"Latest Binance futures global long/short account ratio for {canonical}: "
        f"long={latest.get('longAccount')}, short={latest.get('shortAccount')}, "
        f"ratio={latest.get('longShortRatio')}."
    )


def get_binance_futures_context(symbol: str, curr_date: str) -> str:
    canonical = normalize_binance_symbol(symbol, _default_quote())
    lines = [f"## Binance futures context for {canonical}", ""]
    lines.append(f"- {get_binance_futures_funding_rate(canonical, curr_date)}")
    lines.append(f"- {get_binance_futures_open_interest(canonical, curr_date)}")
    lines.append(f"- {get_binance_futures_long_short_ratio(canonical, curr_date)}")
    return "\n".join(lines)


def _get_binance_indicators_window(symbol: str, indicator: str, curr_date: str, look_back_days: int, market: str) -> str:
    if indicator not in _INDICATOR_DESCRIPTIONS:
        raise ValueError(f"Indicator {indicator} is not supported. Please choose from: {list(_INDICATOR_DESCRIPTIONS.keys())}")

    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = (curr_dt - relativedelta(years=2)).strftime("%Y-%m-%d")
    canonical = normalize_binance_symbol(symbol, _default_quote())
    data = _fetch_klines(canonical, start_date, curr_date, market=market)
    stock_df = wrap(data.copy())
    stock_df["Date"] = pd.to_datetime(stock_df["Date"]).dt.strftime("%Y-%m-%d")
    stock_df[indicator]
    indicator_data = {}
    for _, row in stock_df.iterrows():
        value = row[indicator]
        indicator_data[row["Date"]] = "N/A" if pd.isna(value) else str(value)

    before = curr_dt - relativedelta(days=look_back_days)
    current = curr_dt
    lines = []
    while current >= before:
        date_str = current.strftime("%Y-%m-%d")
        lines.append(f"{date_str}: {indicator_data.get(date_str, 'N/A: No Binance candle for this date')}")
        current -= relativedelta(days=1)

    market_label = "Binance Spot" if market == "spot" else "Binance USD-M Futures"
    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {curr_date} for {canonical} ({market_label}):\n\n"
        + "\n".join(lines)
        + "\n\n"
        + _INDICATOR_DESCRIPTIONS[indicator]
    )


def _fetch_klines(symbol: str, start_date: str, end_date: str, market: str) -> pd.DataFrame:
    start_ms = _to_ms(start_date)
    end_ms = _to_ms(end_date, end_of_day=True)
    base_url = _spot_base_url() if market == "spot" else _futures_base_url()
    endpoint = "/api/v3/klines" if market == "spot" else "/fapi/v1/klines"
    interval = get_config().get("binance_interval", "1d")
    all_rows: list[list[Any]] = []
    next_start = start_ms

    while next_start <= end_ms:
        rows = _request_json(
            f"{base_url}{endpoint}",
            {"symbol": symbol, "interval": interval, "startTime": next_start, "endTime": end_ms, "limit": 1000},
        )
        if not rows:
            break
        all_rows.extend(rows)
        last_open = int(rows[-1][0])
        next_start = last_open + 1
        if len(rows) < 1000:
            break

    if not all_rows:
        raise NoMarketDataError(symbol, symbol, f"no Binance {market} klines returned")
    df = _klines_to_ohlcv_frame(all_rows)
    cutoff = pd.to_datetime(end_date)
    df = df[df["Date"] <= cutoff].drop_duplicates(subset=["Date"]).sort_values("Date")
    if df.empty:
        raise NoMarketDataError(symbol, symbol, f"no Binance {market} rows on or before {end_date}")
    return df


def _klines_to_ohlcv_frame(rows: list[list[Any]]) -> pd.DataFrame:
    data = []
    for row in rows:
        data.append(
            {
                "Date": pd.to_datetime(int(row[0]), unit="ms", utc=True).tz_convert(None).normalize(),
                "Open": float(row[1]),
                "High": float(row[2]),
                "Low": float(row[3]),
                "Close": float(row[4]),
                "Volume": float(row[5]),
            }
        )
    return pd.DataFrame(data)


def _format_ohlcv_report(label: str, requested: str, canonical: str, start_date: str, end_date: str, df: pd.DataFrame) -> str:
    output = df.copy()
    output["Date"] = pd.to_datetime(output["Date"]).dt.strftime("%Y-%m-%d")
    return (
        f"# {label} data for {canonical} from {start_date} to {end_date}\n"
        f"# Requested symbol: {requested}\n"
        f"# Total records: {len(output)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        + output.to_csv(index=False)
    )


def _request_json(url: str, params: dict[str, Any]) -> Any:
    response = requests.get(url, params=params, timeout=20)
    if response.status_code in {418, 429}:
        raise VendorRateLimitError(f"Binance rate limited request to {url}")
    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text
        raise NoMarketDataError(str(params.get("symbol", "")), str(params.get("symbol", "")), f"Binance HTTP {response.status_code}: {detail}")
    return response.json()


def _to_ms(date_str: str, end_of_day: bool = False) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if end_of_day:
        dt = dt + timedelta(days=1) - timedelta(milliseconds=1)
    return int(dt.timestamp() * 1000)


def _default_quote() -> str:
    return str(get_config().get("binance_quote_asset", "USDT"))


def _spot_base_url() -> str:
    return str(get_config().get("binance_spot_base_url", _SPOT_BASE_URL)).rstrip("/")


def _futures_base_url() -> str:
    return str(get_config().get("binance_futures_base_url", _FUTURES_BASE_URL)).rstrip("/")
