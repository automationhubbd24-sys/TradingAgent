"""Local USD-M order book maintained from REST snapshots and depth diffs."""
from __future__ import annotations

from typing import Any, Mapping


class OrderBook:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.last_update_id: int | None = None
        self.resync_required = True

    @staticmethod
    def _levels(rows: Any) -> dict[float, float]:
        result: dict[float, float] = {}
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            price, quantity = float(row[0]), float(row[1])
            if quantity > 0:
                result[price] = quantity
        return result

    def apply_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        update_id = snapshot.get("lastUpdateId")
        if not isinstance(update_id, int):
            self.resync_required = True
            return False
        self.bids, self.asks = self._levels(snapshot.get("bids")), self._levels(snapshot.get("asks"))
        self.last_update_id, self.resync_required = update_id, False
        return True

    def apply_diff(self, diff: Mapping[str, Any]) -> bool:
        first, final, previous = diff.get("U"), diff.get("u"), diff.get("pu")
        if not all(isinstance(value, int) for value in (first, final, previous)) or self.last_update_id is None:
            self.resync_required = True
            return False
        if final <= self.last_update_id:
            return True
        if previous != self.last_update_id or not first <= self.last_update_id + 1 <= final:
            self.resync_required = True
            return False
        for target, rows in ((self.bids, diff.get("b")), (self.asks, diff.get("a"))):
            for price, quantity in self._levels(rows).items():
                target[price] = quantity
            for row in rows or []:
                if isinstance(row, (list, tuple)) and len(row) >= 2 and float(row[1]) == 0:
                    target.pop(float(row[0]), None)
        self.last_update_id = final
        return True

    def summary(self, limit: int = 100) -> dict[str, Any]:
        bids = sorted(self.bids.items(), reverse=True)[:limit]
        asks = sorted(self.asks.items())[:limit]
        bid_volume, ask_volume = sum(q for _, q in bids), sum(q for _, q in asks)
        return {"bids": bids, "asks": asks, "bid_volume": bid_volume, "ask_volume": ask_volume, "imbalance": (bid_volume - ask_volume) / (bid_volume + ask_volume) if bid_volume + ask_volume else None, "last_update_id": self.last_update_id, "resync_required": self.resync_required}
