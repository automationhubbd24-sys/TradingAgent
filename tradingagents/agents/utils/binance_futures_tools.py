from typing import Annotated

from langchain_core.tools import tool

from tradingagents.dataflows.binance import get_binance_futures_context as _get_binance_futures_context


@tool
def get_binance_futures_context(
    symbol: Annotated[str, "Binance futures symbol, e.g. BTCUSDT or BTC/USDT:USDT"],
    curr_date: Annotated[str, "The analysis date, YYYY-mm-dd"],
) -> str:
    """Get public Binance USD-M futures funding, open-interest and long/short context."""
    return _get_binance_futures_context(symbol, curr_date)
